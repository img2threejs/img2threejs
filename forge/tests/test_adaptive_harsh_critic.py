#!/usr/bin/env python3
"""Tests for adaptive, independent, pixel-bound harsh scene critique."""

import copy
import hashlib
import io
import json
import math
import struct
import tempfile
import unittest
import zlib
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from forge.stage4_review.adaptive_harsh_critic import (
    KIND_RESPONSE,
    advance_state,
    base_views,
    build_critic_request,
    init_state,
    main as adaptive_main,
    subdivide_view,
    _validate_state,
)
from forge.stage4_review.render_bridge import (
    ADAPTIVE_BROWSER_RECEIPT_KIND,
    ADAPTIVE_BROWSER_RECEIPT_VERSION,
    canonical_sha256,
    decoded_pixel_sha256,
    _decoded_image_signature,
    _near_identical_image_signatures,
    _normalized_demeaned_luma_mae,
    init_manifest,
    main as render_bridge_main,
    read_manifest,
    record_capture,
    schedule_adaptive_views,
    sha256,
    write_manifest,
)
from forge.stage1_intake.probe_image import probe


def write_blob_png(
    path: Path,
    width: int = 200,
    height: int = 200,
    *,
    variant: int = 0,
    compression_level: int = 9,
    background_value: int = 255,
    global_delta: int = 0,
    pixel_nudge: bool = False,
) -> None:
    """Write a clearly segmentable dark square on white using stdlib only."""
    signature = b"\x89PNG\r\n\x1a\n"

    def chunk(kind: bytes, payload: bytes) -> bytes:
        checksum = zlib.crc32(kind)
        checksum = zlib.crc32(payload, checksum) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)

    rows = bytearray()
    color_value = 20 + ((variant * 37) % 180)
    x_min = 20 + ((variant * 17) % 45)
    x_max = width - 20 - ((variant * 29) % 45)
    y_min = 20 + ((variant * 31) % 45)
    y_max = height - 20 - ((variant * 13) % 45)
    for y in range(height):
        rows.append(0)
        for x in range(width):
            base = (
                color_value
                if x_min <= x < x_max and y_min <= y < y_max
                else background_value
            )
            value = max(0, min(255, base + global_delta))
            if pixel_nudge and x == 0 and y == 0:
                value = value - 1 if value == 255 else value + 1
            color = (value, value, value)
            rows.extend(color)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        signature
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(rows), compression_level))
        + chunk(b"IEND", b"")
    )


def write_color_field_png(
    path: Path,
    color: tuple[int, int, int, int],
    *,
    width: int = 200,
    height: int = 200,
    structured_delta: int = 0,
    hidden_rgb_flip: bool = False,
) -> None:
    """Write an RGBA field for perceptual-collapse adversarial tests."""
    signature = b"\x89PNG\r\n\x1a\n"

    def chunk(kind: bytes, payload: bytes) -> bytes:
        checksum = zlib.crc32(kind)
        checksum = zlib.crc32(payload, checksum) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)

    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            red, green, blue, alpha = color
            if structured_delta:
                sign = (
                    1
                    if ((x * 32 // width) + (y * 32 // height)) % 2
                    else -1
                )
                red = max(0, min(255, red + sign * structured_delta))
                green = max(0, min(255, green + sign * structured_delta))
                blue = max(0, min(255, blue + sign * structured_delta))
            if hidden_rgb_flip and alpha == 0 and (x + y) % 2:
                red, green, blue = 255 - red, 255 - green, 255 - blue
            rows.extend((red, green, blue, alpha))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        signature
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(rows), 9))
        + chunk(b"IEND", b"")
    )


class AdaptiveHarshCriticTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.reference = self.root / "reference.png"
        write_blob_png(self.reference)
        self.manifest_path = self.root / "render-manifest.json"
        self.manifest = init_manifest(
            self.reference,
            "http://127.0.0.1:5173/#/scene",
            self.manifest_path,
            (200, 200),
            1.0,
            "captures",
        )
        write_manifest(self.manifest_path, self.manifest)

    def tearDown(self):
        self.temporary.cleanup()

    def _new_state(self, **options):
        return init_state(self.manifest_path, "creator-agent", **options)

    @staticmethod
    def _camera_matrix(direction):
        z = [float(item) for item in direction]
        z_length = math.sqrt(sum(item * item for item in z))
        z = [item / z_length for item in z]
        up = [0.0, 0.0, 1.0] if abs(z[1]) > 0.9 else [0.0, 1.0, 0.0]
        x = [
            up[1] * z[2] - up[2] * z[1],
            up[2] * z[0] - up[0] * z[2],
            up[0] * z[1] - up[1] * z[0],
        ]
        x_length = math.sqrt(sum(item * item for item in x))
        x = [item / x_length for item in x]
        y = [
            z[1] * x[2] - z[2] * x[1],
            z[2] * x[0] - z[0] * x[2],
            z[0] * x[1] - z[1] * x[0],
        ]
        return [
            x[0], x[1], x[2], 0.0,
            y[0], y[1], y[2], 0.0,
            z[0], z[1], z[2], 0.0,
            z[0] * 3.0, z[1] * 3.0, z[2] * 3.0, 1.0,
        ]

    def _browser_receipt(
        self,
        capture,
        screenshot,
        *,
        ready_value=True,
        runtime_url=None,
        document_url=None,
        scene_build_sha256="3" * 64,
        matrix_world=None,
        nonce="a" * 32,
    ):
        direction = capture["adaptiveCritic"]["direction"]
        image = probe(screenshot)
        css_width, css_height = self.manifest["runtime"]["viewport"]
        dpr = float(self.manifest["runtime"]["devicePixelRatio"])
        return {
            "kind": ADAPTIVE_BROWSER_RECEIPT_KIND,
            "schemaVersion": ADAPTIVE_BROWSER_RECEIPT_VERSION,
            "adapter": {
                "name": "capture_threejs_playwright",
                "version": ADAPTIVE_BROWSER_RECEIPT_VERSION,
            },
            "sessionNonce": nonce,
            "captureId": capture["id"],
            "runtime": {
                "requestedUrl": runtime_url or self.manifest["runtime"]["url"],
                "documentUrl": (
                    document_url
                    if document_url is not None
                    else runtime_url or self.manifest["runtime"]["url"]
                ),
                "documentSha256": "2" * 64,
                "readySignal": {
                    "expression": self.manifest["runtime"]["readySignal"],
                    "value": ready_value,
                    "valueType": "boolean" if isinstance(ready_value, bool) else type(ready_value).__name__,
                },
                "captureContract": {
                    "name": self.manifest["runtime"]["captureContract"],
                    "setCamera": True,
                    "getEvidenceSnapshot": True,
                },
                "snapshotEcho": {
                    "captureId": capture["id"],
                    "sessionNonce": nonce,
                },
                "sceneBuildSha256": scene_build_sha256,
                "objectCount": 12,
            },
            "browser": {"name": "chromium", "version": "test-chromium", "headless": True},
            "camera": {
                "direction": direction,
                "matrixWorld": matrix_world or self._camera_matrix(direction),
                "projectionMatrix": [
                    1.0, 0.0, 0.0, 0.0,
                    0.0, 1.0, 0.0, 0.0,
                    0.0, 0.0, 1.0, 0.0,
                    0.0, 0.0, 0.0, 1.0,
                ],
            },
            "canvas": {
                "selector": "canvas",
                "cssWidth": css_width,
                "cssHeight": css_height,
                "width": round(css_width * dpr),
                "height": round(css_height * dpr),
                "drawingBufferWidth": round(css_width * dpr),
                "drawingBufferHeight": round(css_height * dpr),
                "devicePixelRatio": dpr,
                "webgl": True,
            },
            "screenshot": {
                "sha256": sha256(screenshot),
                "pixelSha256": decoded_pixel_sha256(screenshot),
                "width": image["width"],
                "height": image["height"],
            },
        }

    def _record_next_views(self, state):
        schedule_adaptive_views(self.manifest, state)
        for view in state["nextViews"]:
            capture = next(item for item in self.manifest["captures"] if item["id"] == view["id"])
            screenshot = self.manifest_path.parent / capture["path"]
            variant = 1 + sum(
                1
                for item in self.manifest["captures"]
                if isinstance(item, dict)
                and item.get("role") == "adaptive-critic"
                and item.get("status") == "recorded"
            )
            write_blob_png(screenshot, variant=variant)
            record_capture(
                self.manifest_path,
                self.manifest,
                view["id"],
                screenshot,
                ready_signal=True,
                browser_receipt=self._browser_receipt(capture, screenshot),
            )
        write_manifest(self.manifest_path, self.manifest)
        return state

    def _capture_next_views(self, state):
        self._record_next_views(state)
        return build_critic_request(state, self.manifest_path)

    def _complete_clean_root_round(self, state):
        request = self._capture_next_views(state)
        result = advance_state(state, request, self._response(request))
        self.assertEqual(result["status"], "needs-render")
        self.assertEqual(result["refinementMode"], "minimum-uniform-coverage")
        self.assertEqual(len(result["nextViews"]), 24)
        self.assertTrue(all(view["cell"]["level"] == 1 for view in result["nextViews"]))
        return result

    @staticmethod
    def _response(request, findings_by_view=None, critic_id="critic-agent"):
        findings_by_view = findings_by_view or {}
        reviews = []
        for view in request["views"]:
            templates = findings_by_view.get(view["viewId"], [])
            findings = []
            for template in templates:
                findings.append(
                    {
                        "defectKey": template["defectKey"],
                        "severity": template.get("severity", "major"),
                        "category": template.get("category", "geometry"),
                        "description": template.get("description", "observable defect"),
                        "viewId": view["viewId"],
                        "captureSha256": view["captureSha256"],
                        "direction": view["direction"],
                    }
                )
            reviews.append(
                {
                    "viewId": view["viewId"],
                    "captureSha256": view["captureSha256"],
                    "direction": view["direction"],
                    "verdict": "defect" if findings else "pass",
                    "findings": findings,
                }
            )
        return {
            "kind": KIND_RESPONSE,
            "schemaVersion": 1,
            "requestId": request["requestId"],
            "critic": {
                "id": critic_id,
                "role": "independent-harsh-critic",
                "acknowledgements": {
                    "inspectedPixels": True,
                    "noScoreAveraging": True,
                    "criticalDefectsAreBlocking": True,
                },
            },
            "views": reviews,
        }

    def test_base_sphere_is_six_cardinal_cube_faces(self):
        views = base_views()
        self.assertEqual(len(views), 6)
        self.assertEqual([item["cell"]["face"] for item in views], ["front", "right", "rear", "left", "top", "bottom"])
        self.assertEqual(len({tuple(item["direction"]) for item in views}), 6)
        for view in views:
            self.assertAlmostEqual(sum(value * value for value in view["direction"]), 1.0, places=9)

    def test_init_is_random_and_scopes_capture_ids_and_paths_to_session(self):
        first = self._new_state()
        second = self._new_state()
        self.assertNotEqual(first["sessionId"], second["sessionId"])
        self.assertTrue(
            all(
                view["id"].startswith(f"{first['sessionId']}-harsh-")
                for view in first["nextViews"]
            )
        )
        schedule_adaptive_views(self.manifest, first)
        schedule_adaptive_views(self.manifest, second)
        first_capture = next(
            item
            for item in self.manifest["captures"]
            if item.get("adaptiveCritic", {}).get("sessionId") == first["sessionId"]
        )
        second_capture = next(
            item
            for item in self.manifest["captures"]
            if item.get("adaptiveCritic", {}).get("sessionId") == second["sessionId"]
        )
        self.assertIn(first["sessionId"], first_capture["path"])
        self.assertIn(second["sessionId"], second_capture["path"])
        self.assertNotEqual(first_capture["id"], second_capture["id"])
        self.assertNotEqual(first_capture["path"], second_capture["path"])

    def test_subdivision_is_deterministic_and_shrinks_angular_cell(self):
        parent = base_views()[0]
        first = subdivide_view(parent, 1)
        second = subdivide_view(parent, 1)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 4)
        self.assertTrue(all(item["parentId"] == parent["id"] for item in first))
        self.assertTrue(all(item["angularRadiusDegrees"] < parent["angularRadiusDegrees"] for item in first))

    def test_real_scene_captures_produce_pixel_bound_request(self):
        state = self._new_state()
        request = self._capture_next_views(state)
        self.assertTrue(request["fixedTurntableBaseline"]["passed"])
        self.assertEqual(len(request["views"]), 6)
        payload = {key: value for key, value in request.items() if key not in {"requestId", "requestDigest"}}
        self.assertEqual(request["requestDigest"], canonical_sha256(payload))
        self.assertEqual(request["requestId"], "ahcr-" + request["requestDigest"][:24])
        self.assertEqual(state["pendingRequest"]["canonicalSha256"], request["requestDigest"])
        self.assertEqual(state["scene"]["sceneBuildSha256"], "3" * 64)
        self.assertEqual(len(state["evidenceLedger"]), 6)
        for view in request["views"]:
            path = Path(view["capturePath"])
            self.assertTrue(path.is_file())
            self.assertEqual(view["captureSha256"], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(view["capturePixelSha256"], decoded_pixel_sha256(path))
            self.assertEqual(view["browserEvidenceSha256"], canonical_sha256(view["browserEvidence"]))

    def test_clean_independent_review_passes_without_any_average(self):
        state = self._new_state()
        state = self._complete_clean_root_round(state)
        request = self._capture_next_views(state)
        result = advance_state(state, request, self._response(request))
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["stopReason"], "no-defects-after-minimum-uniform-coverage")
        self.assertEqual(result["scheduledViewCount"], 30)
        self.assertIsNone(result["pendingRequest"])

    def test_critic_must_not_be_the_scene_creator(self):
        state = self._new_state()
        request = self._capture_next_views(state)
        with self.assertRaisesRegex(ValueError, "MUST differ"):
            advance_state(state, request, self._response(request, critic_id="creator-agent"))

    def test_review_and_every_finding_must_bind_real_hash_and_direction(self):
        state = self._new_state()
        request = self._capture_next_views(state)
        view_id = request["views"][0]["viewId"]
        response = self._response(request, {view_id: [{"defectKey": "rear-seam"}]})
        response["views"][0]["findings"][0]["captureSha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "actual pixels"):
            advance_state(state, request, response)

        response = self._response(request, {view_id: [{"defectKey": "rear-seam"}]})
        response["views"][0]["findings"][0]["direction"] = [1.0, 0.0, 0.0]
        with self.assertRaisesRegex(ValueError, "reviewed camera"):
            advance_state(state, request, response)

    def test_request_contract_tampering_and_aggregate_scores_are_rejected(self):
        state = self._new_state()
        request = self._capture_next_views(state)
        tampered = copy.deepcopy(request)
        tampered["criticContract"]["requiredAcknowledgements"] = []
        with self.assertRaisesRegex(ValueError, "canonical digest"):
            advance_state(copy.deepcopy(state), tampered, self._response(request))

        response = self._response(request)
        response["globalScore"] = 1.0
        with self.assertRaisesRegex(ValueError, "never averaging"):
            advance_state(copy.deepcopy(state), request, response)

        # Even an attacker that recomputes the request digest and edits the
        # pending pin cannot smuggle schema-forbidden fields past the hand
        # validator used when jsonschema is unavailable.
        extra = copy.deepcopy(request)
        extra["approved"] = True
        payload = {
            key: value
            for key, value in extra.items()
            if key not in {"requestId", "requestDigest"}
        }
        extra["requestDigest"] = canonical_sha256(payload)
        extra["requestId"] = "ahcr-" + extra["requestDigest"][:24]
        forged_state = copy.deepcopy(state)
        forged_state["pendingRequest"]["canonicalSha256"] = extra["requestDigest"]
        forged_state["pendingRequest"]["requestId"] = extra["requestId"]
        with self.assertRaisesRegex(ValueError, "schema-forbidden"):
            advance_state(forged_state, extra, self._response(extra))

    def test_complete_request_is_canonical_and_pinned_until_consumed(self):
        state = self._new_state()
        request = self._capture_next_views(state)
        with self.assertRaisesRegex(ValueError, "unconsumed pending"):
            build_critic_request(state, self.manifest_path)

        alternate = self.root / "alternate.png"
        write_blob_png(alternate, variant=99)
        tampered = copy.deepcopy(request)
        tampered["views"][0]["capturePath"] = str(alternate)
        tampered["views"][0]["captureSha256"] = sha256(alternate)
        tampered["views"][0]["capturePixelSha256"] = decoded_pixel_sha256(alternate)
        response = self._response(tampered)
        with self.assertRaisesRegex(ValueError, "canonical digest"):
            advance_state(copy.deepcopy(state), tampered, response)

        tampered = copy.deepcopy(request)
        tampered["scene"]["runtimeUrl"] = "http://127.0.0.1:9999/fake"
        with self.assertRaisesRegex(ValueError, "canonical digest"):
            advance_state(copy.deepcopy(state), tampered, self._response(tampered))

        result = advance_state(state, request, self._response(request))
        self.assertIsNone(result["pendingRequest"])
        self.assertEqual(result["status"], "needs-render")

    def test_request_cli_persists_pending_digest_in_state(self):
        state = self._new_state()
        self._record_next_views(state)
        state_path = self.root / "state.json"
        request_path = self.root / "request.json"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual(
            adaptive_main(
                [
                    "request",
                    "--state",
                    str(state_path),
                    "--out",
                    str(request_path),
                ]
            ),
            0,
        )
        saved_state = json.loads(state_path.read_text(encoding="utf-8"))
        saved_request = json.loads(request_path.read_text(encoding="utf-8"))
        self.assertEqual(
            saved_state["pendingRequest"]["canonicalSha256"],
            saved_request["requestDigest"],
        )
        self.assertEqual(saved_state["pendingRequest"]["requestId"], saved_request["requestId"])

    def test_advance_has_one_canonical_in_place_transition_and_no_out_fork(self):
        state = self._new_state()
        self._record_next_views(state)
        state_path = self.root / "state.json"
        request_path = self.root / "request.json"
        response_path = self.root / "response.json"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(
                adaptive_main(
                    ["request", "--state", str(state_path), "--out", str(request_path)]
                ),
                0,
            )
        request = json.loads(request_path.read_text(encoding="utf-8"))
        response_path.write_text(json.dumps(self._response(request)), encoding="utf-8")
        before_rejected_out = state_path.read_bytes()
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                adaptive_main(
                    [
                        "advance",
                        "--state",
                        str(state_path),
                        "--request",
                        str(request_path),
                        "--reviews",
                        str(response_path),
                        "--out",
                        str(self.root / "forbidden-snapshot.json"),
                    ]
                )
        self.assertEqual(raised.exception.code, 2)
        self.assertEqual(state_path.read_bytes(), before_rejected_out)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(
                adaptive_main(
                    [
                        "advance",
                        "--state",
                        str(state_path),
                        "--request",
                        str(request_path),
                        "--reviews",
                        str(response_path),
                        "--in-place",
                    ]
                ),
                0,
            )
        source = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertIsNone(source["pendingRequest"])
        self.assertEqual(source["currentRound"], 1)

    def test_manual_record_cli_cannot_mint_adaptive_browser_evidence(self):
        state = self._new_state()
        schedule_adaptive_views(self.manifest, state)
        write_manifest(self.manifest_path, self.manifest)
        capture = next(item for item in self.manifest["captures"] if item.get("role") == "adaptive-critic")
        screenshot = self.root / "manual.png"
        write_blob_png(screenshot, variant=1)
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            exit_code = render_bridge_main(
                [
                    "record",
                    "--manifest",
                    str(self.manifest_path),
                    "--capture-id",
                    capture["id"],
                    "--screenshot",
                    str(screenshot),
                ]
            )
        self.assertEqual(exit_code, 2)
        self.assertIn("cannot be registered from an arbitrary PNG", stderr.getvalue())
        reloaded = read_manifest(self.manifest_path)
        reloaded_capture = next(item for item in reloaded["captures"] if item["id"] == capture["id"])
        self.assertEqual(reloaded_capture["status"], "pending")

        stderr = io.StringIO()
        with redirect_stderr(stderr):
            exit_code = render_bridge_main(
                [
                    "record",
                    "--manifest",
                    str(self.manifest_path),
                    "--capture-id",
                    capture["id"],
                    "--screenshot",
                    str(screenshot),
                    "--ready-signal",
                    "window.__IMG2THREEJS_READY__",
                ]
            )
        self.assertEqual(exit_code, 2)
        self.assertIn("strict browser boolean true", stderr.getvalue())

    def test_browser_receipt_requires_strict_ready_runtime_and_actual_camera_matrix(self):
        state = self._new_state()
        schedule_adaptive_views(self.manifest, state)
        capture = next(item for item in self.manifest["captures"] if item.get("role") == "adaptive-critic")
        screenshot = self.root / "candidate.png"
        write_blob_png(screenshot, variant=1)

        receipt = self._browser_receipt(capture, screenshot, ready_value="true")
        with self.assertRaisesRegex(ValueError, "ready contract"):
            record_capture(
                self.manifest_path,
                self.manifest,
                capture["id"],
                screenshot,
                ready_signal=True,
                browser_receipt=receipt,
            )

        receipt = self._browser_receipt(capture, screenshot)
        receipt["canvas"]["drawingBufferWidth"] = 199
        with self.assertRaisesRegex(ValueError, "drawing buffer"):
            record_capture(
                self.manifest_path,
                self.manifest,
                capture["id"],
                screenshot,
                ready_signal=True,
                browser_receipt=receipt,
            )

        receipt = self._browser_receipt(capture, screenshot)
        receipt["unexpectedAttestation"] = True
        with self.assertRaisesRegex(ValueError, "schema-forbidden"):
            record_capture(
                self.manifest_path,
                self.manifest,
                capture["id"],
                screenshot,
                ready_signal=True,
                browser_receipt=receipt,
            )

        receipt = self._browser_receipt(capture, screenshot, runtime_url="http://127.0.0.1:9999/fake")
        with self.assertRaisesRegex(ValueError, "runtime URL"):
            record_capture(
                self.manifest_path,
                self.manifest,
                capture["id"],
                screenshot,
                ready_signal=True,
                browser_receipt=receipt,
            )

        bad_matrix = self._camera_matrix(capture["adaptiveCritic"]["direction"])
        bad_matrix[8:11] = [1.0, 0.0, 0.0]
        receipt = self._browser_receipt(capture, screenshot, matrix_world=bad_matrix)
        with self.assertRaisesRegex(ValueError, "matrix does not encode planned direction"):
            record_capture(
                self.manifest_path,
                self.manifest,
                capture["id"],
                screenshot,
                ready_signal=True,
                browser_receipt=receipt,
            )

        captures = [item for item in self.manifest["captures"] if item.get("role") == "adaptive-critic"]
        first_path = self.root / "same-build-first.png"
        second_path = self.root / "changed-build-second.png"
        write_blob_png(first_path, variant=10)
        write_blob_png(second_path, variant=11)
        record_capture(
            self.manifest_path,
            self.manifest,
            captures[0]["id"],
            first_path,
            ready_signal=True,
            browser_receipt=self._browser_receipt(captures[0], first_path),
        )
        with self.assertRaisesRegex(ValueError, "different scene build digests"):
            record_capture(
                self.manifest_path,
                self.manifest,
                captures[1]["id"],
                second_path,
                ready_signal=True,
                browser_receipt=self._browser_receipt(
                    captures[1], second_path, scene_build_sha256="4" * 64
                ),
            )

    def test_duplicate_pixels_reencoded_for_distinct_directions_fail_closed(self):
        state = self._new_state()
        schedule_adaptive_views(self.manifest, state)
        captures = [item for item in self.manifest["captures"] if item.get("role") == "adaptive-critic"]
        first_path = self.root / "first.png"
        second_path = self.root / "second.png"
        write_blob_png(first_path, variant=7, compression_level=1)
        write_blob_png(second_path, variant=7, compression_level=9)
        self.assertNotEqual(sha256(first_path), sha256(second_path))
        self.assertEqual(decoded_pixel_sha256(first_path), decoded_pixel_sha256(second_path))
        record_capture(
            self.manifest_path,
            self.manifest,
            captures[0]["id"],
            first_path,
            ready_signal=True,
            browser_receipt=self._browser_receipt(captures[0], first_path),
        )
        with self.assertRaisesRegex(ValueError, "duplicate/collapsed"):
            record_capture(
                self.manifest_path,
                self.manifest,
                captures[1]["id"],
                second_path,
                ready_signal=True,
                browser_receipt=self._browser_receipt(captures[1], second_path),
            )
        self.assertEqual(captures[1]["status"], "pending")

    def test_one_pixel_and_one_lsb_noise_cannot_evade_near_duplicate_gate(self):
        state = self._new_state()
        schedule_adaptive_views(self.manifest, state)
        captures = [item for item in self.manifest["captures"] if item.get("role") == "adaptive-critic"]
        first_path = self.root / "perceptual-first.png"
        one_pixel_path = self.root / "perceptual-one-pixel.png"
        one_lsb_path = self.root / "perceptual-one-lsb.png"
        distinct_path = self.root / "perceptual-distinct.png"
        write_blob_png(first_path, variant=7, background_value=230)
        write_blob_png(one_pixel_path, variant=7, background_value=230, pixel_nudge=True)
        write_blob_png(one_lsb_path, variant=7, background_value=230, global_delta=1)
        write_blob_png(distinct_path, variant=8, background_value=230)
        record_capture(
            self.manifest_path,
            self.manifest,
            captures[0]["id"],
            first_path,
            ready_signal=True,
            browser_receipt=self._browser_receipt(captures[0], first_path),
        )
        for adversarial_path in (one_pixel_path, one_lsb_path):
            with self.assertRaisesRegex(ValueError, "near-identical/collapsed"):
                record_capture(
                    self.manifest_path,
                    self.manifest,
                    captures[1]["id"],
                    adversarial_path,
                    ready_signal=True,
                    browser_receipt=self._browser_receipt(captures[1], adversarial_path),
                )
        recorded = record_capture(
            self.manifest_path,
            self.manifest,
            captures[1]["id"],
            distinct_path,
            ready_signal=True,
            browser_receipt=self._browser_receipt(captures[1], distinct_path),
        )
        self.assertEqual(recorded["status"], "recorded")

    def test_structured_two_lsb_noise_blocks_even_when_phash_diverges(self):
        state = self._new_state()
        schedule_adaptive_views(self.manifest, state)
        captures = [item for item in self.manifest["captures"] if item.get("role") == "adaptive-critic"]
        base_path = self.root / "gray-base.png"
        noisy_path = self.root / "gray-structured-plus-minus-2.png"
        write_color_field_png(base_path, (128, 128, 128, 255))
        write_color_field_png(
            noisy_path,
            (128, 128, 128, 255),
            structured_delta=2,
        )
        collapsed, phash_distance, visible_rgb_mae = _near_identical_image_signatures(
            _decoded_image_signature(base_path),
            _decoded_image_signature(noisy_path),
        )
        self.assertGreater(phash_distance, 2)
        self.assertAlmostEqual(visible_rgb_mae, 2 / 255, places=4)
        self.assertTrue(collapsed)
        record_capture(
            self.manifest_path,
            self.manifest,
            captures[0]["id"],
            base_path,
            ready_signal=True,
            browser_receipt=self._browser_receipt(captures[0], base_path),
        )
        with self.assertRaisesRegex(ValueError, "near-identical/collapsed"):
            record_capture(
                self.manifest_path,
                self.manifest,
                captures[1]["id"],
                noisy_path,
                ready_signal=True,
                browser_receipt=self._browser_receipt(captures[1], noisy_path),
            )

    def test_global_and_structured_one_to_three_lsb_noise_all_fail_closed(self):
        base_path = self.root / "gray-128.png"
        write_color_field_png(base_path, (128, 128, 128, 255))
        base_signature = _decoded_image_signature(base_path)
        for delta in (1, 2, 3):
            with self.subTest(kind="global", delta=delta):
                shifted_path = self.root / f"gray-global-plus-{delta}.png"
                write_color_field_png(
                    shifted_path,
                    (128 + delta, 128 + delta, 128 + delta, 255),
                )
                shifted_signature = _decoded_image_signature(shifted_path)
                collapsed, phash_distance, visible_rgb_mae = (
                    _near_identical_image_signatures(base_signature, shifted_signature)
                )
                self.assertEqual(phash_distance, 0)
                self.assertAlmostEqual(visible_rgb_mae, delta / 255, places=4)
                self.assertAlmostEqual(
                    _normalized_demeaned_luma_mae(base_signature, shifted_signature),
                    0.0,
                    places=8,
                )
                self.assertTrue(collapsed)
            with self.subTest(kind="structured", delta=delta):
                structured_path = self.root / f"gray-structured-plus-minus-{delta}.png"
                write_color_field_png(
                    structured_path,
                    (128, 128, 128, 255),
                    structured_delta=delta,
                )
                structured_signature = _decoded_image_signature(structured_path)
                collapsed, _phash_distance, visible_rgb_mae = (
                    _near_identical_image_signatures(base_signature, structured_signature)
                )
                self.assertAlmostEqual(visible_rgb_mae, delta / 255, places=4)
                self.assertTrue(collapsed)

        state = self._new_state()
        schedule_adaptive_views(self.manifest, state)
        captures = [
            item for item in self.manifest["captures"]
            if item.get("role") == "adaptive-critic"
        ]
        plus_three_path = self.root / "gray-plus-three-runtime.png"
        write_color_field_png(plus_three_path, (131, 131, 131, 255))
        record_capture(
            self.manifest_path,
            self.manifest,
            captures[0]["id"],
            base_path,
            ready_signal=True,
            browser_receipt=self._browser_receipt(captures[0], base_path),
        )
        with self.assertRaisesRegex(ValueError, "near-identical/collapsed"):
            record_capture(
                self.manifest_path,
                self.manifest,
                captures[1]["id"],
                plus_three_path,
                ready_signal=True,
                browser_receipt=self._browser_receipt(captures[1], plus_three_path),
            )

    def test_isoluminant_distinct_colors_are_not_collapsed(self):
        state = self._new_state()
        schedule_adaptive_views(self.manifest, state)
        captures = [item for item in self.manifest["captures"] if item.get("role") == "adaptive-critic"]
        red_path = self.root / "visible-red.png"
        green_path = self.root / "visible-dark-green.png"
        write_color_field_png(red_path, (255, 0, 0, 255))
        write_color_field_png(green_path, (0, 76, 0, 255))
        collapsed, _phash_distance, visible_rgb_mae = _near_identical_image_signatures(
            _decoded_image_signature(red_path),
            _decoded_image_signature(green_path),
        )
        self.assertGreater(visible_rgb_mae, 0.4)
        self.assertFalse(collapsed)
        record_capture(
            self.manifest_path,
            self.manifest,
            captures[0]["id"],
            red_path,
            ready_signal=True,
            browser_receipt=self._browser_receipt(captures[0], red_path),
        )
        result = record_capture(
            self.manifest_path,
            self.manifest,
            captures[1]["id"],
            green_path,
            ready_signal=True,
            browser_receipt=self._browser_receipt(captures[1], green_path),
        )
        self.assertEqual(result["status"], "recorded")

    def test_hidden_rgb_under_transparency_is_still_collapsed(self):
        state = self._new_state()
        schedule_adaptive_views(self.manifest, state)
        captures = [item for item in self.manifest["captures"] if item.get("role") == "adaptive-critic"]
        first_path = self.root / "transparent-red.png"
        second_path = self.root / "transparent-hidden-noise.png"
        write_color_field_png(first_path, (255, 0, 0, 0))
        write_color_field_png(
            second_path,
            (255, 0, 0, 0),
            hidden_rgb_flip=True,
        )
        self.assertNotEqual(decoded_pixel_sha256(first_path), decoded_pixel_sha256(second_path))
        record_capture(
            self.manifest_path,
            self.manifest,
            captures[0]["id"],
            first_path,
            ready_signal=True,
            browser_receipt=self._browser_receipt(captures[0], first_path),
        )
        with self.assertRaisesRegex(ValueError, "near-identical/collapsed"):
            record_capture(
                self.manifest_path,
                self.manifest,
                captures[1]["id"],
                second_path,
                ready_signal=True,
                browser_receipt=self._browser_receipt(captures[1], second_path),
            )

    def test_scene_lock_and_historical_evidence_ledger_fail_closed(self):
        state = self._new_state()
        request = self._capture_next_views(state)
        locked = copy.deepcopy(state)
        locked["scene"]["sceneBuildSha256"] = "4" * 64
        locked["pendingRequest"] = None
        locked["evidenceLedger"] = []
        with self.assertRaisesRegex(ValueError, "first-round state lock"):
            build_critic_request(locked, self.manifest_path)

        state = advance_state(state, request, self._response(request))
        schedule_adaptive_views(self.manifest, state)
        previous = next(
            item
            for item in self.manifest["captures"]
            if item.get("adaptiveCritic", {}).get("round") == 0
        )
        original = self.manifest_path.parent / previous["path"]
        replay_path = self.root / "moved-old-evidence.png"
        replay_path.write_bytes(original.read_bytes())
        previous["path"] = str(replay_path)
        for index, view in enumerate(state["nextViews"]):
            capture = next(item for item in self.manifest["captures"] if item["id"] == view["id"])
            screenshot = self.manifest_path.parent / capture["path"]
            write_blob_png(screenshot, variant=40 + index)
            record_capture(
                self.manifest_path,
                self.manifest,
                view["id"],
                screenshot,
                ready_signal=True,
                browser_receipt=self._browser_receipt(capture, screenshot),
            )
        write_manifest(self.manifest_path, self.manifest)
        with self.assertRaisesRegex(ValueError, "historical adaptive evidence changed"):
            build_critic_request(state, self.manifest_path)

    def test_failed_request_build_is_transactional_for_direct_api_callers(self):
        state = self._new_state()
        self._record_next_views(state)
        before = copy.deepcopy(state)
        with mock.patch(
            "forge.stage4_review.adaptive_harsh_critic._base_turntable_result",
            side_effect=ValueError("forced baseline failure"),
        ):
            with self.assertRaisesRegex(ValueError, "forced baseline failure"):
                build_critic_request(state, self.manifest_path)
        self.assertEqual(state, before)
        self.assertIsNone(state["scene"]["sceneBuildSha256"])
        self.assertEqual(state["evidenceLedger"], [])
        self.assertIsNone(state["pendingRequest"])

    def test_view_cell_and_policy_hand_validation_matches_fail_closed_schema(self):
        cases = []
        missing_bound = self._new_state()
        missing_bound["nextViews"][0]["cell"].pop("uMin")
        cases.append(("missing-bound", missing_bound))
        outside_bound = self._new_state()
        outside_bound["nextViews"][0]["cell"]["uMin"] = 2
        cases.append(("outside-bound", outside_bound))
        reversed_bound = self._new_state()
        reversed_bound["nextViews"][0]["cell"]["uMin"] = 0.5
        reversed_bound["nextViews"][0]["cell"]["uMax"] = -0.5
        cases.append(("reversed-bound", reversed_bound))
        shrunk_root = self._new_state()
        shrunk_root["nextViews"][0]["cell"]["uMin"] = -0.1
        shrunk_root["nextViews"][0]["cell"]["uMax"] = 0.1
        shrunk_root["nextViews"][0]["cell"]["vMin"] = -0.1
        shrunk_root["nextViews"][0]["cell"]["vMax"] = 0.1
        cases.append(("path-bounds-coverage", shrunk_root))
        negative_level = self._new_state()
        negative_level["nextViews"][0]["cell"]["level"] = -3
        cases.append(("negative-level", negative_level))
        wrong_path_level = self._new_state()
        wrong_path_level["nextViews"][0]["cell"]["path"] = "0"
        cases.append(("path-level", wrong_path_level))
        wrong_radius = self._new_state()
        wrong_radius["nextViews"][0]["angularRadiusDegrees"] /= 2
        cases.append(("angular-radius", wrong_radius))
        for label, invalid in cases:
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    _validate_state(invalid)

    def test_state_required_scene_and_status_action_fields_fail_closed(self):
        cases = []
        missing_root = self._new_state()
        missing_root.pop("pendingRequest")
        cases.append(("missing-root", missing_root))
        empty_manifest_path = self._new_state()
        empty_manifest_path["scene"]["manifestPath"] = ""
        cases.append(("empty-manifest-path", empty_manifest_path))
        bad_manifest_hash = self._new_state()
        bad_manifest_hash["scene"]["manifestSha256AtInit"] = "not-a-sha"
        cases.append(("manifest-init-hash", bad_manifest_hash))
        empty_runtime = self._new_state()
        empty_runtime["scene"]["runtimeUrl"] = ""
        cases.append(("empty-runtime", empty_runtime))
        bad_action = self._new_state()
        bad_action["action"] = "continue"
        cases.append(("needs-render-action", bad_action))
        fake_pass = self._new_state()
        fake_pass["status"] = "passed"
        fake_pass["action"] = "continue"
        cases.append(("passed-with-views", fake_pass))
        empty_reference_capture = self._new_state()
        empty_reference_capture["evidenceLedger"] = [
            {
                "viewId": empty_reference_capture["nextViews"][0]["id"],
                "round": 0,
                "capturePath": "capture.png",
                "captureSha256": "1" * 64,
                "capturePixelSha256": "2" * 64,
                "browserEvidenceSha256": "3" * 64,
                "sceneBuildSha256": "4" * 64,
                "direction": empty_reference_capture["nextViews"][0]["direction"],
                "referenceCapturePath": "",
                "referenceCaptureSha256": "5" * 64,
            }
        ]
        empty_reference_capture["pendingRequest"] = {
            "requestId": "ahcr-" + "a" * 24,
            "canonicalSha256": "a" * 64,
            "round": 0,
            "manifestSha256": "b" * 64,
            "viewIds": [empty_reference_capture["nextViews"][0]["id"]],
        }
        cases.append(("empty-reference-capture", empty_reference_capture))
        for label, invalid in cases:
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    _validate_state(invalid)

        schema = json.loads(
            Path("docs/specs/adaptive-harsh-critic.v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            schema["$defs"]["captureEvidence"]["properties"]
            ["referenceCapturePath"]["minLength"],
            1,
        )

        for field, value in (
            ("allowHoles", "false"),
            ("baseCoverage", "pretend-full-sphere"),
            ("subdivision", "skip-defects"),
        ):
            invalid = self._new_state()
            invalid["policy"][field] = value
            with self.subTest(policy_field=field):
                with self.assertRaises(ValueError):
                    _validate_state(invalid)

    def test_per_view_reference_capture_is_pinned_for_advance_and_history(self):
        state = self._new_state()
        self._record_next_views(state)
        first_capture = next(
            item for item in self.manifest["captures"] if item.get("role") == "adaptive-critic"
        )
        reference_capture = self.root / "per-view-reference.png"
        write_blob_png(reference_capture, variant=71)
        first_capture["reference"] = {
            "path": reference_capture.relative_to(self.root).as_posix(),
            "status": "recorded",
            "readySignal": True,
            "consoleErrors": [],
            "screenshotSha256": sha256(reference_capture),
            "image": probe(reference_capture),
        }
        write_manifest(self.manifest_path, self.manifest)
        request = build_critic_request(state, self.manifest_path)
        bound_view = next(
            item for item in request["views"] if item["viewId"] == first_capture["id"]
        )
        self.assertEqual(bound_view["referenceCaptureSha256"], sha256(reference_capture))
        ledger_entry = next(
            item for item in state["evidenceLedger"] if item["viewId"] == first_capture["id"]
        )
        self.assertEqual(
            ledger_entry["referenceCaptureSha256"], bound_view["referenceCaptureSha256"]
        )
        original_reference_bytes = reference_capture.read_bytes()
        write_blob_png(reference_capture, variant=72)
        with self.assertRaisesRegex(ValueError, "reference capture hash changed"):
            advance_state(copy.deepcopy(state), request, self._response(request))

        reference_capture.write_bytes(original_reference_bytes)
        advanced = advance_state(state, request, self._response(request))
        write_blob_png(reference_capture, variant=73)
        before_failed_history = copy.deepcopy(advanced)
        with self.assertRaisesRegex(ValueError, "reference capture hash changed"):
            build_critic_request(advanced, self.manifest_path)
        self.assertEqual(advanced, before_failed_history)

    def test_reference_source_file_is_rehashed_at_request_and_advance(self):
        state = self._new_state()
        self._record_next_views(state)
        before_request = copy.deepcopy(state)
        request = build_critic_request(state, self.manifest_path)
        original_reference_bytes = self.reference.read_bytes()
        write_blob_png(self.reference, variant=88)
        with self.assertRaisesRegex(ValueError, "reference source file hash"):
            build_critic_request(before_request, self.manifest_path)
        with self.assertRaisesRegex(ValueError, "reference source file hash"):
            advance_state(copy.deepcopy(state), request, self._response(request))
        self.reference.write_bytes(original_reference_bytes)

    def test_glb_session_requires_a_recorded_reference_for_every_adaptive_view(self):
        self.manifest["reference"]["kind"] = "glb"
        write_manifest(self.manifest_path, self.manifest)
        state = self._new_state()
        self._record_next_views(state)
        with self.assertRaisesRegex(ValueError, "no recorded browser reference capture"):
            build_critic_request(state, self.manifest_path)
        downgraded = copy.deepcopy(state)
        downgraded["scene"]["referenceKind"] = "image"
        with self.assertRaisesRegex(ValueError, "reference kind changed"):
            build_critic_request(downgraded, self.manifest_path)

    def test_browser_document_url_allows_origin_slash_but_rejects_redirect(self):
        self.manifest["runtime"]["url"] = "http://127.0.0.1:5173"
        write_manifest(self.manifest_path, self.manifest)
        state = self._new_state()
        schedule_adaptive_views(self.manifest, state)
        captures = [item for item in self.manifest["captures"] if item.get("role") == "adaptive-critic"]
        first_path = self.root / "origin-normalized.png"
        second_path = self.root / "redirected.png"
        write_blob_png(first_path, variant=91)
        write_blob_png(second_path, variant=92)
        result = record_capture(
            self.manifest_path,
            self.manifest,
            captures[0]["id"],
            first_path,
            ready_signal=True,
            browser_receipt=self._browser_receipt(
                captures[0], first_path, document_url="http://127.0.0.1:5173/"
            ),
        )
        self.assertEqual(result["status"], "recorded")
        with self.assertRaisesRegex(ValueError, "runtime URL"):
            record_capture(
                self.manifest_path,
                self.manifest,
                captures[1]["id"],
                second_path,
                ready_signal=True,
                browser_receipt=self._browser_receipt(
                    captures[1],
                    second_path,
                    document_url="http://127.0.0.1:5173/redirected",
                ),
            )

    def test_hand_validator_rejects_schema_forbidden_response_fields(self):
        state = self._new_state()
        request = self._capture_next_views(state)
        response = self._response(request)
        response["critic"]["approved"] = True
        with self.assertRaisesRegex(ValueError, "schema-forbidden"):
            advance_state(state, request, response)

    def test_critical_finding_cannot_be_averaged_away_and_creates_next_views(self):
        state = self._complete_clean_root_round(self._new_state())
        request = self._capture_next_views(state)
        target = request["views"][0]["viewId"]
        response = self._response(
            request,
            {target: [{"defectKey": "through-skull", "severity": "critical"}]},
        )
        result = advance_state(state, request, response)
        self.assertNotEqual(result["status"], "passed")
        self.assertEqual(result["status"], "needs-render")
        self.assertEqual(len(result["nextViews"]), 4)
        self.assertEqual(result["refinementMode"], "defect-directed")
        self.assertEqual(result["rounds"][1]["criticalCount"], 1)
        self.assertFalse(result["rounds"][1]["criticalRulePassed"])

    def test_same_defect_in_child_round_converges_repeated_defect(self):
        state = self._complete_clean_root_round(self._new_state())
        request0 = self._capture_next_views(state)
        target0 = request0["views"][0]["viewId"]
        state = advance_state(
            state,
            request0,
            self._response(request0, {target0: [{"defectKey": "floating-hat", "severity": "major"}]}),
        )
        request1 = self._capture_next_views(state)
        target1 = request1["views"][0]["viewId"]
        state = advance_state(
            state,
            request1,
            self._response(request1, {target1: [{"defectKey": "floating-hat", "severity": "major"}]}),
        )
        self.assertEqual(state["status"], "blocked")
        self.assertEqual(state["stopReason"], "repeated-defect")
        self.assertEqual(state["action"], "refine-code")

    def test_plateau_with_renamed_but_unimproved_defect_stops(self):
        state = self._complete_clean_root_round(self._new_state(plateau_rounds=1))
        request0 = self._capture_next_views(state)
        target0 = request0["views"][0]["viewId"]
        state = advance_state(
            state,
            request0,
            self._response(request0, {target0: [{"defectKey": "bad-a", "severity": "major"}]}),
        )
        request1 = self._capture_next_views(state)
        target1 = request1["views"][0]["viewId"]
        state = advance_state(
            state,
            request1,
            self._response(request1, {target1: [{"defectKey": "bad-b", "severity": "major"}]}),
        )
        self.assertEqual(state["status"], "blocked")
        self.assertEqual(state["stopReason"], "plateau")

    def test_max_view_budget_below_uniform_floor_blocks_clean_root(self):
        state = self._new_state(max_views=29)
        request = self._capture_next_views(state)
        result = advance_state(
            state,
            request,
            self._response(request),
        )
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["stopReason"], "max-views-before-minimum-coverage")
        self.assertEqual(result["nextViews"], [])

    def test_missing_view_review_and_mutated_capture_fail_closed(self):
        state = self._new_state()
        request = self._capture_next_views(state)
        response = self._response(request)
        response["views"].pop()
        with self.assertRaisesRegex(ValueError, "review every requested view"):
            advance_state(copy.deepcopy(state), request, response)

        response = self._response(request)
        Path(request["views"][0]["capturePath"]).write_bytes(b"mutated")
        with self.assertRaisesRegex(ValueError, "hash changed"):
            advance_state(copy.deepcopy(state), request, response)

    def test_render_bridge_reimport_is_idempotent_and_rejects_direction_collision(self):
        state = self._new_state()
        first = schedule_adaptive_views(self.manifest, state)
        second = schedule_adaptive_views(self.manifest, state)
        self.assertEqual(len(first["scheduled"]), 6)
        self.assertEqual(len(second["alreadyPresent"]), 6)
        self.assertEqual(len([item for item in self.manifest["captures"] if item.get("role") == "adaptive-critic"]), 6)
        self.assertEqual(len(self.manifest["evidence"]["adaptiveCriticPlans"]), 1)

        collision_id = state["nextViews"][0]["id"]
        collision_capture = next(
            item for item in self.manifest["captures"] if item.get("id") == collision_id
        )
        collision_capture["adaptiveCritic"]["direction"] = [1.0, 0.0, 0.0]
        with self.assertRaisesRegex(ValueError, "collision"):
            schedule_adaptive_views(self.manifest, state)

    def test_schedule_rejects_path_escape_and_contains_all_generated_evidence(self):
        state = self._new_state()
        before = copy.deepcopy(self.manifest)
        malicious = copy.deepcopy(state)
        malicious["nextViews"][0]["id"] = (
            f"{state['sessionId']}-harsh-/../../../../../../escaped"
        )
        with self.assertRaises(ValueError):
            schedule_adaptive_views(
                self.manifest,
                malicious,
                manifest_path=self.manifest_path,
            )
        self.assertEqual(self.manifest, before)

        for unsafe_output in (
            "../escaped",
            "/absolute/escaped",
            "C:/escaped",
            "captures\\..\\escaped",
        ):
            with self.subTest(output_dir=unsafe_output):
                with self.assertRaises(ValueError):
                    schedule_adaptive_views(
                        self.manifest,
                        state,
                        manifest_path=self.manifest_path,
                        output_dir=unsafe_output,
                    )
                self.assertEqual(self.manifest, before)

        plan_path = self.root / "safe-plan-unsafe-output.json"
        plan_path.write_text(json.dumps(state), encoding="utf-8")
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            exit_code = render_bridge_main(
                [
                    "schedule-adaptive",
                    "--manifest",
                    str(self.manifest_path),
                    "--plan",
                    str(plan_path),
                    "--output-dir",
                    "../escaped",
                ]
            )
        self.assertEqual(exit_code, 2)
        self.assertIn("unsafe relative path component", stderr.getvalue())
        self.assertEqual(read_manifest(self.manifest_path), before)

        glb_manifest = copy.deepcopy(self.manifest)
        glb_manifest["reference"]["kind"] = "glb"
        glb_state = copy.deepcopy(state)
        glb_state["scene"]["referenceKind"] = "glb"
        result = schedule_adaptive_views(
            glb_manifest,
            glb_state,
            manifest_path=self.manifest_path,
        )
        self.assertEqual(len(result["scheduled"]), 6)
        evidence_root = self.manifest_path.parent.resolve()
        for capture in (
            item for item in glb_manifest["captures"]
            if item.get("role") == "adaptive-critic"
        ):
            paths = [capture["path"]]
            paths.extend(record["path"] for record in capture["passes"].values())
            paths.append(capture["reference"]["path"])
            paths.extend(
                record["path"] for record in capture["reference"]["passes"].values()
            )
            for path_value in paths:
                resolved = (evidence_root / Path(path_value)).resolve()
                self.assertTrue(resolved.is_relative_to(evidence_root))
                self.assertNotIn("..", Path(path_value).parts)


if __name__ == "__main__":
    unittest.main(verbosity=2)
