#!/usr/bin/env python3
"""Optional local vision evidence for img2threejs.

This module is deliberately outside ``forge`` so the core pipeline remains Python
stdlib-only. Every command emits provenance JSON. The outputs are evidence for the
agent and deterministic gates; they never choose geometry or approve a build pass.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUNTIME = ROOT / "runtime" / "vision"
DEFAULT_MODELS = DEFAULT_RUNTIME / "models"
DEFAULT_HF_CACHE = DEFAULT_RUNTIME / "huggingface"
SAM_MODEL = "facebook/sam2.1-hiera-tiny"
DEPTH_MODEL = "depth-anything/Depth-Anything-V2-Small-hf"
FACE_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/latest/face_landmarker.task"
)
POSE_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
    "pose_landmarker_lite/float16/latest/pose_landmarker_lite.task"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _versions() -> dict[str, str]:
    names = ("mediapipe", "numpy", "opencv-contrib-python", "Pillow", "torch", "torchvision", "transformers")
    versions: dict[str, str] = {}
    for name in names:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "missing"
    return versions


def _device_name(torch_module: Any) -> str:
    if getattr(torch_module.backends, "mps", None) and torch_module.backends.mps.is_available():
        return "mps"
    if torch_module.cuda.is_available():
        return "cuda"
    return "cpu"


def command_health(args: argparse.Namespace) -> int:
    import torch

    payload = {
        "status": "ok",
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "device": _device_name(torch),
        "packages": _versions(),
        "models": {
            "sam2": SAM_MODEL,
            "depth": DEPTH_MODEL,
            "faceTask": str(Path(args.models_dir) / "face_landmarker.task"),
            "poseTask": str(Path(args.models_dir) / "pose_landmarker_lite.task"),
        },
        "boundary": "evidence-only; never geometry truth or pass authority",
    }
    if args.json_out:
        _write_json(Path(args.json_out), payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _download(url: str, destination: Path) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        with urllib.request.urlopen(url, timeout=180) as response:
            destination.write_bytes(response.read())
    return {
        "url": url,
        "path": str(destination),
        "bytes": destination.stat().st_size,
        "sha256": _sha256(destination),
    }


def command_prefetch(args: argparse.Namespace) -> int:
    from transformers import AutoImageProcessor, AutoModelForDepthEstimation, Sam2Model, Sam2Processor

    models_dir = Path(args.models_dir)
    hf_cache = Path(args.hf_cache)
    models_dir.mkdir(parents=True, exist_ok=True)
    hf_cache.mkdir(parents=True, exist_ok=True)

    sam_processor = Sam2Processor.from_pretrained(SAM_MODEL, cache_dir=hf_cache, use_fast=False)
    sam_model = Sam2Model.from_pretrained(SAM_MODEL, cache_dir=hf_cache)
    depth_processor = AutoImageProcessor.from_pretrained(DEPTH_MODEL, cache_dir=hf_cache, use_fast=False)
    depth_model = AutoModelForDepthEstimation.from_pretrained(DEPTH_MODEL, cache_dir=hf_cache)

    payload = {
        "status": "ok",
        "packages": _versions(),
        "huggingFaceCache": str(hf_cache),
        "models": {
            "sam2": {
                "id": SAM_MODEL,
                "revision": getattr(sam_model.config, "_commit_hash", None)
                or getattr(sam_processor, "_commit_hash", None),
            },
            "depth": {
                "id": DEPTH_MODEL,
                "revision": getattr(depth_model.config, "_commit_hash", None)
                or getattr(depth_processor, "_commit_hash", None),
            },
            "faceTask": _download(FACE_MODEL_URL, models_dir / "face_landmarker.task"),
            "poseTask": _download(POSE_MODEL_URL, models_dir / "pose_landmarker_lite.task"),
        },
        "boundary": "downloaded models produce priors/evidence only",
    }
    manifest = models_dir / "model_manifest.json"
    _write_json(manifest, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _load_image(path: Path) -> Any:
    from PIL import Image

    return Image.open(path).convert("RGB")


def command_segment(args: argparse.Namespace) -> int:
    import numpy as np
    import torch
    from PIL import Image
    from transformers import Sam2Model, Sam2Processor

    image_path = Path(args.image).expanduser().resolve()
    image = _load_image(image_path)
    device = _device_name(torch)
    processor = Sam2Processor.from_pretrained(
        SAM_MODEL,
        cache_dir=args.hf_cache,
        local_files_only=True,
        use_fast=False,
    )
    model = Sam2Model.from_pretrained(
        SAM_MODEL,
        cache_dir=args.hf_cache,
        local_files_only=True,
    ).to(device)
    model.eval()

    points = args.point or [[image.width / 2.0, image.height / 2.0]]
    inputs = processor(
        images=image,
        input_points=[[points]],
        input_labels=[[[1] * len(points)]],
        return_tensors="pt",
    )
    original_sizes = inputs["original_sizes"].clone()
    model_inputs = {
        key: value.to(device) if hasattr(value, "to") else value
        for key, value in inputs.items()
    }
    with torch.inference_mode():
        outputs = model(**model_inputs)
    masks = processor.post_process_masks(outputs.pred_masks.cpu(), original_sizes)[0]
    scores = outputs.iou_scores.detach().cpu().reshape(-1)
    best = int(torch.argmax(scores).item())
    mask_tensor = masks.reshape(-1, masks.shape[-2], masks.shape[-1])[best]
    mask = (mask_tensor.numpy() > 0).astype(np.uint8) * 255

    output = Path(args.out).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(mask).save(output)
    payload = {
        "kind": "segmentation-mask",
        "model": SAM_MODEL,
        "sourceImage": str(image_path),
        "sourceSha256": _sha256(image_path),
        "output": str(output),
        "outputSha256": _sha256(output),
        "prompt": {"positivePoints": [[round(float(px), 3), round(float(py), 3)] for px, py in points]},
        "predictedIou": round(float(scores[best].item()), 6),
        "foregroundPixels": int((mask > 0).sum()),
        "imageSize": [image.width, image.height],
        "device": device,
        "boundary": "mask evidence only; agent must confirm the selected subject/component",
    }
    json_out = Path(args.json_out or f"{output}.json")
    _write_json(json_out, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def command_depth(args: argparse.Namespace) -> int:
    import numpy as np
    import torch
    from PIL import Image
    from transformers import AutoImageProcessor, AutoModelForDepthEstimation

    image_path = Path(args.image).expanduser().resolve()
    image = _load_image(image_path)
    device = _device_name(torch)
    processor = AutoImageProcessor.from_pretrained(
        DEPTH_MODEL,
        cache_dir=args.hf_cache,
        local_files_only=True,
        use_fast=False,
    )
    model = AutoModelForDepthEstimation.from_pretrained(
        DEPTH_MODEL,
        cache_dir=args.hf_cache,
        local_files_only=True,
    ).to(device)
    model.eval()
    inputs = processor(images=image, return_tensors="pt")
    model_inputs = {
        key: value.to(device) if hasattr(value, "to") else value
        for key, value in inputs.items()
    }
    with torch.inference_mode():
        outputs = model(**model_inputs)
    result = processor.post_process_depth_estimation(
        outputs,
        target_sizes=[(image.height, image.width)],
    )[0]["predicted_depth"].detach().cpu().numpy()
    minimum = float(result.min())
    maximum = float(result.max())
    normalized = (result - minimum) / max(maximum - minimum, 1e-9)
    pixels = (normalized * 65535.0).round().astype(np.uint16)

    output = Path(args.out).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(pixels).save(output)
    payload = {
        "kind": "relative-depth-prior",
        "model": DEPTH_MODEL,
        "sourceImage": str(image_path),
        "sourceSha256": _sha256(image_path),
        "output": str(output),
        "outputSha256": _sha256(output),
        "rawRange": [minimum, maximum],
        "imageSize": [image.width, image.height],
        "device": device,
        "boundary": "relative depth prior only; never metric scale or hidden geometry truth",
    }
    json_out = Path(args.json_out or f"{output}.json")
    _write_json(json_out, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _category_payload(category: Any) -> dict[str, Any]:
    return {
        "index": int(category.index),
        "score": float(category.score),
        "name": category.category_name or category.display_name or "",
    }


def command_landmarks(args: argparse.Namespace) -> int:
    import mediapipe as mp
    from mediapipe.tasks import python
    from mediapipe.tasks.python import vision

    image_path = Path(args.image).expanduser().resolve()
    model_path = Path(args.model or Path(args.models_dir) / (
        "face_landmarker.task" if args.kind == "face" else "pose_landmarker_lite.task"
    ))
    if not model_path.exists():
        raise FileNotFoundError(f"missing MediaPipe model {model_path}; run the prefetch command first")
    image = mp.Image.create_from_file(str(image_path))
    base = python.BaseOptions(
        model_asset_path=str(model_path),
        delegate=python.BaseOptions.Delegate.CPU,
    )

    if args.kind == "face":
        options = vision.FaceLandmarkerOptions(
            base_options=base,
            running_mode=vision.RunningMode.IMAGE,
            output_face_blendshapes=True,
            output_facial_transformation_matrixes=True,
            num_faces=1,
        )
        with vision.FaceLandmarker.create_from_options(options) as task:
            result = task.detect(image)
        landmarks = [
            [{"x": p.x, "y": p.y, "z": p.z, "visibility": getattr(p, "visibility", None)} for p in face]
            for face in result.face_landmarks
        ]
        extras = {
            "blendshapes": [[_category_payload(item) for item in face] for face in result.face_blendshapes],
            "transformationMatrices": [matrix.tolist() for matrix in result.facial_transformation_matrixes],
        }
    else:
        options = vision.PoseLandmarkerOptions(
            base_options=base,
            running_mode=vision.RunningMode.IMAGE,
            output_segmentation_masks=False,
            num_poses=1,
        )
        with vision.PoseLandmarker.create_from_options(options) as task:
            result = task.detect(image)
        landmarks = [
            [{"x": p.x, "y": p.y, "z": p.z, "visibility": p.visibility} for p in pose]
            for pose in result.pose_landmarks
        ]
        extras = {
            "worldLandmarks": [
                [{"x": p.x, "y": p.y, "z": p.z, "visibility": p.visibility} for p in pose]
                for pose in result.pose_world_landmarks
            ]
        }

    payload = {
        "kind": f"{args.kind}-landmarks",
        "model": {
            "path": str(model_path),
            "sha256": _sha256(model_path),
        },
        "sourceImage": str(image_path),
        "sourceSha256": _sha256(image_path),
        "landmarks": landmarks,
        **extras,
        "boundary": "observed landmark evidence; geometry and anatomy still require agent review",
    }
    output = Path(args.out).expanduser().resolve()
    _write_json(output, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.set_defaults(func=None)
    subparsers = parser.add_subparsers(dest="command", required=True)

    health = subparsers.add_parser("health", help="Verify the isolated vision environment")
    health.add_argument("--models-dir", default=str(DEFAULT_MODELS))
    health.add_argument("--json-out")
    health.set_defaults(func=command_health)

    prefetch = subparsers.add_parser("prefetch", help="Download the pinned local vision models")
    prefetch.add_argument("--models-dir", default=str(DEFAULT_MODELS))
    prefetch.add_argument("--hf-cache", default=str(DEFAULT_HF_CACHE))
    prefetch.set_defaults(func=command_prefetch)

    segment = subparsers.add_parser("segment", help="Create a SAM2 point-prompted mask")
    segment.add_argument("image")
    # Repeatable. SAM2 resolves a single point to whatever region that point sits in, which on a
    # character in a busy scene is one garment, not the character: a single click on the sash of a
    # forest render returned the sash alone at predictedIou 0.604. Several positive points spread over
    # head, torso and legs tell it they belong to ONE object.
    segment.add_argument(
        "--point", nargs=2, type=float, metavar=("X", "Y"), action="append", dest="point",
    )
    segment.add_argument("--out", required=True)
    segment.add_argument("--json-out")
    segment.add_argument("--hf-cache", default=str(DEFAULT_HF_CACHE))
    segment.set_defaults(func=command_segment)

    depth = subparsers.add_parser("depth", help="Create a relative Depth Anything V2 prior")
    depth.add_argument("image")
    depth.add_argument("--out", required=True)
    depth.add_argument("--json-out")
    depth.add_argument("--hf-cache", default=str(DEFAULT_HF_CACHE))
    depth.set_defaults(func=command_depth)

    landmarks = subparsers.add_parser("landmarks", help="Extract face or pose landmarks with MediaPipe")
    landmarks.add_argument("kind", choices=("face", "pose"))
    landmarks.add_argument("image")
    landmarks.add_argument("--out", required=True)
    landmarks.add_argument("--model")
    landmarks.add_argument("--models-dir", default=str(DEFAULT_MODELS))
    landmarks.set_defaults(func=command_landmarks)
    return parser


def main(argv: list[str]) -> int:
    os.environ.setdefault("HF_HOME", str(DEFAULT_HF_CACHE))
    os.environ.setdefault("MPLCONFIGDIR", str(DEFAULT_RUNTIME / "matplotlib"))
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


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
