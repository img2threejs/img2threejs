#!/usr/bin/env python3
"""Optional Playwright adapter for the Python↔Three.js render bridge.

The target page must expose:

    window.__IMG2THREEJS_READY__ = true
    window.__IMG2THREEJS_CAPTURE__.setCamera(cameraSpec)

The adapter captures the actual browser canvas/viewport. It never renders a
replacement scene in Python and fails closed when the runtime contract is absent.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from forge.stage4_review.render_bridge import (  # noqa: E402
    find_capture,
    manifest_path,
    read_manifest,
    record_capture,
    record_capture_pass,
    record_reference_capture,
    PASS_IDS,
    ADAPTIVE_BROWSER_RECEIPT_KIND,
    ADAPTIVE_BROWSER_RECEIPT_VERSION,
    decoded_pixel_sha256,
    sha256,
    write_manifest,
)
from forge.stage1_intake.probe_image import probe  # noqa: E402


def capture(manifest_path_value: Path, capture_ids: list[str], headed: bool, timeout_ms: int, mode: str) -> dict:
    try:
        from playwright.sync_api import Error as PlaywrightError  # type: ignore[import-not-found]
        from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. Install it in an isolated environment "
            "(`python3 -m pip install playwright` and `playwright install chromium`) "
            "or use the existing Chrome DevTools MCP adapter."
        ) from exc

    manifest = read_manifest(manifest_path_value)
    runtime = manifest.get("runtime", {})
    viewport = runtime.get("viewport", [620, 1000])
    dpr = float(runtime.get("devicePixelRatio", 1))
    url = str(runtime.get("url", ""))
    if not url:
        raise ValueError("manifest runtime.url is missing")
    reference = manifest.get("reference", {})
    fidelity_v2 = manifest.get("fidelityTrack") == "glb-mediated-v2"
    if mode == "reference":
        if reference.get("kind") != "glb":
            raise ValueError("--mode reference requires a GLB reference manifest")
        if not reference.get("browserUrl"):
            raise ValueError("GLB reference manifest needs reference.browserUrl for the browser adapter")
    selected = capture_ids or [str(item["id"]) for item in manifest.get("captures", [])]
    console_errors: list[str] = []
    page_errors: list[str] = []
    browser_info: dict = {}
    session_nonce = secrets.token_hex(16)

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=not headed)
            browser_info = {
                "adapter": "playwright",
                "adapterVersion": ADAPTIVE_BROWSER_RECEIPT_VERSION,
                "browser": "chromium",
                "browserVersion": browser.version,
                "headless": not headed,
                "sessionNonce": session_nonce,
            }
            context = browser.new_context(
                viewport={"width": int(viewport[0]), "height": int(viewport[1])},
                device_scale_factor=dpr,
            )
            context.add_init_script(
                "Object.defineProperty(window, '__IMG2THREEJS_ADAPTER_SESSION__', "
                f"{{value: '{session_nonce}', writable: false, configurable: false}});"
            )
            page = context.new_page()
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_function("() => window.__IMG2THREEJS_READY__ === true", timeout=timeout_ms)

            if fidelity_v2:
                pass_contract = page.evaluate(
                    "() => ({capturePass: typeof window.__IMG2THREEJS_CAPTURE__?.capturePass === 'function'})"
                )
                if not pass_contract.get("capturePass"):
                    raise RuntimeError(
                        "GLB-mediated-v2 route must expose "
                        "window.__IMG2THREEJS_CAPTURE__.capturePass({passId, mode})"
                    )

            mode_result = page.evaluate(
                """
                async ({mode, reference}) => {
                  const api = window.__IMG2THREEJS_CAPTURE__;
                  if (mode !== 'reference') return {ok: true};
                  if (!api || typeof api.setReferenceMode !== 'function') {
                    return {ok: false, reason: 'window.__IMG2THREEJS_CAPTURE__.setReferenceMode is missing'};
                  }
                  await api.setReferenceMode({kind: 'glb', url: reference.browserUrl});
                  return {ok: true};
                }
                """,
                {"mode": mode, "reference": reference},
            )
            if not mode_result.get("ok"):
                raise RuntimeError(str(mode_result.get("reason", "reference mode contract failed")))

            for capture_id in selected:
                capture_spec = find_capture(manifest, capture_id)
                adaptive = capture_spec.get("role") == "adaptive-critic"
                result = page.evaluate(
                    """
                    async (camera) => {
                      const api = window.__IMG2THREEJS_CAPTURE__;
                      if (!api || typeof api.setCamera !== 'function') {
                        return {ok: false, reason: 'window.__IMG2THREEJS_CAPTURE__.setCamera is missing'};
                      }
                      await api.setCamera(camera);
                      return {ok: true};
                    }
                    """,
                    capture_spec,
                )
                if not result.get("ok"):
                    raise RuntimeError(str(result.get("reason", "camera contract failed")))
                page.evaluate(
                    """
                    async (frames) => {
                      for (let i = 0; i < frames; i += 1) {
                        await new Promise((resolve) => requestAnimationFrame(resolve));
                      }
                    }
                    """,
                    2,
                )
                canvas = page.evaluate(
                    """
                    () => {
                      const canvas = document.querySelector('canvas');
                      if (!canvas) return null;
                      const gl = canvas.getContext('webgl2') || canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
                      const rect = canvas.getBoundingClientRect();
                      return {
                        cssWidth: Math.round(rect.width),
                        cssHeight: Math.round(rect.height),
                        width: canvas.width,
                        height: canvas.height,
                        drawingBufferWidth: gl?.drawingBufferWidth || 0,
                        drawingBufferHeight: gl?.drawingBufferHeight || 0,
                        devicePixelRatio: window.devicePixelRatio,
                        webgl: Boolean(gl),
                      };
                    }
                    """
                )
                if (
                    not canvas
                    or canvas["cssWidth"] <= 0
                    or canvas["cssHeight"] <= 0
                    or canvas["width"] <= 0
                    or canvas["height"] <= 0
                    or canvas["drawingBufferWidth"] <= 0
                    or canvas["drawingBufferHeight"] <= 0
                    or canvas.get("webgl") is not True
                ):
                    raise RuntimeError("Three.js WebGL canvas is missing or has zero dimensions")
                evidence_snapshot = None
                # A GLB reference capture uses the same scheduled camera but
                # does not mint procedural adaptive evidence; requiring the
                # procedural getEvidenceSnapshot contract here would reject a
                # valid reference-only route for data that is never consumed.
                if adaptive and mode != "reference":
                    evidence_snapshot = page.evaluate(
                        """
                        async ({captureId, sessionNonce}) => {
                          const api = window.__IMG2THREEJS_CAPTURE__;
                          if (!api || typeof api.setCamera !== 'function') {
                            return {ok: false, reason: 'window.__IMG2THREEJS_CAPTURE__.setCamera is missing'};
                          }
                          if (typeof api.getEvidenceSnapshot !== 'function') {
                            return {ok: false, reason: 'window.__IMG2THREEJS_CAPTURE__.getEvidenceSnapshot is missing'};
                          }
                          if (window.__IMG2THREEJS_READY__ !== true) {
                            return {ok: false, reason: 'window.__IMG2THREEJS_READY__ is not strict boolean true'};
                          }
                          const snapshot = await api.getEvidenceSnapshot({captureId, sessionNonce});
                          return {ok: true, snapshot};
                        }
                        """,
                        {"captureId": capture_id, "sessionNonce": session_nonce},
                    )
                    if evidence_snapshot.get("ok") is not True:
                        raise RuntimeError(str(evidence_snapshot.get("reason", "adaptive evidence snapshot failed")))
                    evidence_snapshot = evidence_snapshot.get("snapshot")
                    if not isinstance(evidence_snapshot, dict):
                        raise RuntimeError("adaptive getEvidenceSnapshot returned no object")
                    if evidence_snapshot.get("captureId") != capture_id:
                        raise RuntimeError("adaptive evidence snapshot captureId mismatch")
                    if evidence_snapshot.get("sessionNonce") != session_nonce:
                        raise RuntimeError("adaptive evidence snapshot session nonce mismatch")
                if mode == "reference":
                    reference_spec = capture_spec.get("reference")
                    if not isinstance(reference_spec, dict) or not reference_spec.get("path"):
                        raise RuntimeError(f"capture {capture_id} has no GLB reference path")
                    screenshot = manifest_path(manifest_path_value, str(reference_spec["path"]))
                else:
                    screenshot = manifest_path(manifest_path_value, str(capture_spec["path"]))
                screenshot.parent.mkdir(parents=True, exist_ok=True)
                if adaptive and mode != "reference":
                    page.locator("canvas").screenshot(path=str(screenshot))
                elif fidelity_v2:
                    # The capture contract may return a selector when the route has a
                    # dedicated pass canvas; default to the target Three.js canvas.
                    pass_result = page.evaluate(
                        """
                        async ({passId, mode}) => {
                          const api = window.__IMG2THREEJS_CAPTURE__;
                          const result = await api.capturePass({passId, mode});
                          return result || {ok: true};
                        }
                        """,
                        {"passId": "beauty", "mode": mode},
                    )
                    if pass_result.get("ok") is False:
                        raise RuntimeError(str(pass_result.get("reason", "beauty pass failed")))
                    selector = str(pass_result.get("selector", "canvas"))
                    page.locator(selector).screenshot(path=str(screenshot))
                else:
                    page.screenshot(path=str(screenshot), full_page=False)
                ready_value = page.evaluate("() => window.__IMG2THREEJS_READY__")
                if mode == "reference":
                    record_reference_capture(
                        manifest_path_value,
                        manifest,
                        capture_id,
                        screenshot,
                        ready_signal=ready_value,
                        console_errors=console_errors + page_errors,
                    )
                else:
                    browser_receipt = None
                    if adaptive:
                        assert isinstance(evidence_snapshot, dict)
                        image = probe(screenshot)
                        document_sha256 = hashlib.sha256(page.content().encode("utf-8")).hexdigest()
                        browser_receipt = {
                            "kind": ADAPTIVE_BROWSER_RECEIPT_KIND,
                            "schemaVersion": ADAPTIVE_BROWSER_RECEIPT_VERSION,
                            "adapter": {
                                "name": "capture_threejs_playwright",
                                "version": ADAPTIVE_BROWSER_RECEIPT_VERSION,
                            },
                            "sessionNonce": session_nonce,
                            "captureId": capture_id,
                            "runtime": {
                                "requestedUrl": url,
                                "documentUrl": page.url,
                                "documentSha256": document_sha256,
                                "readySignal": {
                                    "expression": runtime.get("readySignal"),
                                    "value": ready_value,
                                    "valueType": "boolean" if isinstance(ready_value, bool) else type(ready_value).__name__,
                                },
                                "captureContract": {
                                    "name": runtime.get("captureContract"),
                                    "setCamera": True,
                                    "getEvidenceSnapshot": True,
                                },
                                "snapshotEcho": {
                                    "captureId": evidence_snapshot.get("captureId"),
                                    "sessionNonce": evidence_snapshot.get("sessionNonce"),
                                },
                                "sceneBuildSha256": evidence_snapshot.get("sceneBuildSha256"),
                                "objectCount": evidence_snapshot.get("objectCount"),
                            },
                            "browser": {
                                "name": "chromium",
                                "version": browser.version,
                                "headless": not headed,
                            },
                            "camera": evidence_snapshot.get("camera"),
                            "canvas": {"selector": "canvas", **canvas},
                            "screenshot": {
                                "sha256": sha256(screenshot),
                                "pixelSha256": decoded_pixel_sha256(screenshot),
                                "width": image.get("width"),
                                "height": image.get("height"),
                            },
                        }
                    record_capture(
                        manifest_path_value,
                        manifest,
                        capture_id,
                        screenshot,
                        ready_signal=ready_value,
                        console_errors=console_errors + page_errors,
                        browser_snapshot={"canvas": canvas},
                        browser_receipt=browser_receipt,
                    )

                if fidelity_v2:
                    for pass_id in PASS_IDS:
                        if pass_id == "beauty":
                            pass_path = screenshot
                        else:
                            target = capture_spec.get("reference") if mode == "reference" else capture_spec
                            pass_path = manifest_path(manifest_path_value, str(target["passes"][pass_id]["path"]))
                            pass_path.parent.mkdir(parents=True, exist_ok=True)
                            pass_result = page.evaluate(
                                """
                                async ({passId, mode}) => {
                                  const api = window.__IMG2THREEJS_CAPTURE__;
                                  const result = await api.capturePass({passId, mode});
                                  return result || {ok: true};
                                }
                                """,
                                {"passId": pass_id, "mode": mode},
                            )
                            if pass_result.get("ok") is False:
                                raise RuntimeError(str(pass_result.get("reason", "diagnostic pass failed")))
                            selector = str(pass_result.get("selector", "canvas"))
                            page.locator(selector).screenshot(path=str(pass_path))
                        record_capture_pass(
                            manifest_path_value,
                            manifest,
                            capture_id,
                            pass_id,
                            pass_path,
                            reference=mode == "reference",
                        )

            browser.close()
    except PlaywrightError as exc:
        raise RuntimeError(f"Playwright capture failed: {exc}") from exc

    manifest.setdefault("evidence", {})["browser"] = browser_info
    manifest["evidence"]["consoleErrors"] = console_errors + page_errors
    write_manifest(manifest_path_value, manifest)
    return {"captured": selected, "mode": mode, "browser": browser_info, "consoleErrors": console_errors + page_errors}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--capture-id", action="append", default=[], help="capture only this id; repeatable")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--timeout-ms", type=int, default=30000)
    parser.add_argument("--mode", choices=("procedural", "reference"), default="procedural")
    args = parser.parse_args(argv)
    try:
        result = capture(args.manifest.expanduser().resolve(), args.capture_id, args.headed, args.timeout_ms, args.mode)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if not result["consoleErrors"] else 1
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
