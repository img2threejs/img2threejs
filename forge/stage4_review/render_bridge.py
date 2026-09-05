#!/usr/bin/env python3
"""Deterministic manifest and evidence controller for browser Three.js renders.

This module does not render a model. It creates a camera batch, validates saved
screenshots, records provenance, and delegates deterministic image checks to the
existing review gates. A browser adapter (Chrome MCP or Playwright) must produce
the actual Three.js pixels.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STAGE1 = Path(__file__).resolve().parents[1] / "stage1_intake"
sys.path.insert(0, str(STAGE1))
from probe_image import probe  # noqa: E402
from probe_glb import probe_glb  # noqa: E402

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


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def record_capture(
    manifest_path_value: Path,
    manifest: dict[str, Any],
    capture_id: str,
    screenshot: Path,
    ready_signal: Any = True,
    console_errors: list[str] | None = None,
    browser_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    screenshot = screenshot.expanduser().resolve()
    if not screenshot.is_file():
        raise ValueError(f"screenshot does not exist: {screenshot}")
    image = probe(screenshot)
    if not image.get("type") or not image.get("width") or not image.get("height"):
        raise ValueError(f"screenshot is not readable: {screenshot}")
    errors = list(console_errors or [])
    capture = find_capture(manifest, capture_id)
    capture["path"] = portable_path(manifest_path_value, screenshot)
    capture["status"] = "recorded"
    capture["recordedAt"] = now_utc()
    capture["readySignal"] = ready_signal
    capture["screenshotSha256"] = sha256(screenshot)
    capture["image"] = image
    capture["consoleErrors"] = errors
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

    record = commands.add_parser("record", help="record a browser-produced screenshot into the manifest")
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
        raise ValueError(f"unknown command: {args.command}")
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
