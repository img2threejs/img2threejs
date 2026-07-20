#!/usr/bin/env python3
"""Emit a projection/UV-bake descriptor for photo-projected texturing.

This script does not perform GPU projective texturing and it does not
rasterize or bake any pixels. Actual camera-space projection of the
(ideally de-lit) reference image onto the fitted mesh, and the bake of that
projection into the mesh's UV space, is a Three.js runtime operation (a
projective ShaderMaterial, e.g. the `three-projected-material` technique).
What this script produces is the plan: a validated, versioned descriptor
that records which camera, which source image(s), which mesh, and which
projection settings the Three.js generator/agent should use to actually run
that bake, plus the back/side inference strategy for regions the camera
never saw.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


VALID_PROJECTION_MODES = ("perspective-camera-projection", "orthographic-front-projection", "triplanar-fallback")
VALID_UNSEEN_STRATEGIES = ("mirror-symmetry", "palette-continue", "request-additional-view", "leave-unprojected")


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def load_camera(camera_arg: str | None) -> tuple[dict[str, Any] | None, list[str]]:
    warnings: list[str] = []
    if not camera_arg:
        warnings.append("no --camera reference supplied; projection will use an identity/front camera assumption")
        return None, warnings
    path = Path(camera_arg).expanduser()
    if not path.exists():
        warnings.append(f"--camera path {camera_arg!r} does not exist; recording it as an opaque reference id instead")
        return {"reference": camera_arg}, warnings
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        warnings.append(f"could not parse --camera JSON at {path}: {exc}; recording it as an opaque reference id")
        return {"reference": camera_arg}, warnings
    camera = data.get("referenceCamera", data) if isinstance(data, dict) else None
    if not isinstance(camera, dict):
        warnings.append(f"--camera JSON at {path} did not contain a referenceCamera object")
        return {"reference": camera_arg}, warnings
    return camera, warnings


def build_descriptor(args: argparse.Namespace) -> dict[str, Any]:
    camera, camera_warnings = load_camera(args.camera)
    warnings = list(camera_warnings)

    source_images: dict[str, str] = {"reference": str(Path(args.reference_image).expanduser())}
    if args.delit_image:
        source_images["delit"] = str(Path(args.delit_image).expanduser())
    else:
        warnings.append(
            "no --delit-image supplied; projecting the raw reference will bake its lighting into the mesh "
            "unless the runtime applies its own de-lighting pass first"
        )

    unseen_strategy = args.unseen_strategy
    unseen_confidence = {
        "mirror-symmetry": 0.45,
        "palette-continue": 0.3,
        "request-additional-view": 0.0,
        "leave-unprojected": 0.0,
    }[unseen_strategy]

    bake_steps = [
        "load the fitted mesh identified by targetMeshId and its UV layout",
        "load sourceImages.delit if present, otherwise sourceImages.reference, as the projection source texture",
        "construct a projection camera in the Three.js scene from the camera block (fovDegrees, aspect, orientation, position)",
        f"apply {args.projection_mode} to project the source texture onto mesh surfaces facing the projection camera within tolerance",
        f"for surfaces outside the camera's view frustum or facing away, apply the '{unseen_strategy}' strategy",
        f"rasterize the resulting camera-space projection into a {args.texture_size}x{args.texture_size} UV-space texture",
        "flag any UV texels that received no projected sample (fully unseen regions) in the bake output metadata",
        "hand the baked texture back to the material pipeline as the projected albedo input",
    ]

    return {
        "projectedTextureBake": {
            "version": "1.0",
            "generator": "stage3_build/bake_projected_texture.py",
            "status": "descriptor-only; no pixels are baked or rasterized by this script",
            "targetMeshId": args.mesh_id,
            "projectionMode": args.projection_mode,
            "textureSize": args.texture_size,
            "camera": camera,
            "sourceImages": source_images,
            "unseenRegionStrategy": {
                "mode": unseen_strategy,
                "confidence": unseen_confidence,
                "note": "Regions the reference camera never saw (back, occluded folds, underside) are inferred, not observed.",
            },
            "runtimeApproach": (
                "actual projective texturing and UV bake happen in the Three.js runtime via a projective "
                "ShaderMaterial (the three-projected-material approach) or an equivalent camera-space "
                "projection shader; this descriptor only records the plan for that step"
            ),
            "bakeSteps": bake_steps,
            "limitations": [
                "this script performs no image sampling, projection math, or UV rasterization",
                "camera accuracy is inherited from whatever produced the camera block; an unrefined camera will misalign the projection",
                "unseen-region inference is a heuristic guess, not observed geometry or texture",
                "the resulting bake still needs a rendered overlay review against the reference image before being trusted",
            ]
            + warnings,
            "note": (
                "Feed this descriptor to the Three.js generator/agent to run the actual projection and bake, "
                "then re-render and visually compare the baked mesh against the reference image before "
                "accepting the result as final."
            ),
        }
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reference-image", required=True, help="Path to the original reference photo")
    parser.add_argument("--delit-image", help="Path to a de-lit albedo produced by stage1_intake/delight_albedo.py, if available")
    parser.add_argument("--camera", help="Path to a referenceCamera JSON produced by stage1_intake/solve_camera_pose.py")
    parser.add_argument("--mesh-id", required=True, help="Identifier of the target mesh/node to project onto")
    parser.add_argument(
        "--projection-mode",
        choices=VALID_PROJECTION_MODES,
        default="perspective-camera-projection",
        help="Projection technique the Three.js runtime should use (default perspective-camera-projection)",
    )
    parser.add_argument("--texture-size", type=int, default=1024, help="Target baked texture resolution (square)")
    parser.add_argument(
        "--unseen-strategy",
        choices=VALID_UNSEEN_STRATEGIES,
        default="mirror-symmetry",
        help="How to handle mesh regions outside the reference camera's view (default mirror-symmetry)",
    )
    parser.add_argument("--out", type=Path, help="Write the descriptor JSON to this path")
    args = parser.parse_args(argv)

    if args.texture_size <= 0:
        parser.error("--texture-size must be positive")

    try:
        descriptor = build_descriptor(args)
        text = json.dumps(descriptor, indent=2, ensure_ascii=False)
        if args.out:
            out_path = args.out.expanduser().resolve()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(text + "\n", encoding="utf-8")
        print(text)
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
