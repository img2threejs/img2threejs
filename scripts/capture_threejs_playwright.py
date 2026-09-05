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
import json
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
    write_manifest,
)


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

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=not headed)
            browser_info = {"adapter": "playwright", "browser": "chromium", "headless": not headed}
            context = browser.new_context(
                viewport={"width": int(viewport[0]), "height": int(viewport[1])},
                device_scale_factor=dpr,
            )
            page = context.new_page()
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_function("() => Boolean(window.__IMG2THREEJS_READY__)", timeout=timeout_ms)

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
                      return canvas ? {width: canvas.width, height: canvas.height} : null;
                    }
                    """
                )
                if not canvas or canvas["width"] <= 0 or canvas["height"] <= 0:
                    raise RuntimeError("Three.js canvas has zero dimensions")
                if mode == "reference":
                    reference_spec = capture_spec.get("reference")
                    if not isinstance(reference_spec, dict) or not reference_spec.get("path"):
                        raise RuntimeError(f"capture {capture_id} has no GLB reference path")
                    screenshot = manifest_path(manifest_path_value, str(reference_spec["path"]))
                else:
                    screenshot = manifest_path(manifest_path_value, str(capture_spec["path"]))
                screenshot.parent.mkdir(parents=True, exist_ok=True)
                if fidelity_v2:
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
                    record_capture(
                        manifest_path_value,
                        manifest,
                        capture_id,
                        screenshot,
                        ready_signal=ready_value,
                        console_errors=console_errors + page_errors,
                        browser_snapshot={"canvas": canvas},
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
    import sys
    from pathlib import Path

    try:
        sys.path.insert(0, str(next(
            parent / "forge" / "_shared"
            for parent in Path(__file__).resolve().parents
            if (parent / "forge" / "_shared" / "cli_run.py").is_file()
        )))
        from cli_run import run_entry
    except (ImportError, StopIteration):
        # vendored/fixture copies without the forge runtime: run bare, no pipe handling
        def run_entry(main_fn, argv=None):
            return main_fn(sys.argv[1:] if argv is None else argv)

    raise SystemExit(run_entry(main))
