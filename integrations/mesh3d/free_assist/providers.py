"""One-shot provider adapters. No adapter performs routing, retry, or fallback."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Protocol

from .model import AssistFailure, GenerationRequest, RawGeneration
from .registry import provider_spec


class MeshProvider(Protocol):
    def generate(self, request: GenerationRequest, run_dir: Path) -> RawGeneration: ...


def _token() -> str | None:
    try:
        from huggingface_hub import get_token

        return get_token()
    except ImportError as exc:
        raise AssistFailure(
            "authentication_required",
            "huggingface_hub is required; install it and run `hf auth login`",
        ) from exc


def _find_glb(value: Any) -> Path | None:
    if isinstance(value, (str, Path)):
        candidate = Path(value)
        return (
            candidate
            if candidate.suffix.lower() == ".glb" and candidate.is_file()
            else None
        )
    if isinstance(value, dict):
        for key in ("path", "video", "value"):
            candidate = _find_glb(value.get(key))
            if candidate:
                return candidate
        for item in value.values():
            candidate = _find_glb(item)
            if candidate:
                return candidate
    if isinstance(value, (list, tuple)):
        for item in value:
            candidate = _find_glb(item)
            if candidate:
                return candidate
    return None


class TrellisZeroGpuProvider:
    def generate(self, request: GenerationRequest, run_dir: Path) -> RawGeneration:
        try:
            from gradio_client import Client, handle_file
        except ImportError as exc:
            raise AssistFailure(
                "provider_unavailable",
                "gradio_client is required for hosted generation",
            ) from exc
        spec = provider_spec(request.provider_id)
        client = Client(spec.endpoint, token=_token(), verbose=False)
        primary = handle_file(str(request.images[0].resolve()))
        extra = [
            {"image": handle_file(str(path.resolve())), "caption": None}
            for path in request.images[1:]
        ]
        parameters = {
            "seed": int(request.parameters.get("seed", 0)),
            "ss_guidance_strength": float(
                request.parameters.get("ssGuidanceStrength", 7.5)
            ),
            "ss_sampling_steps": int(request.parameters.get("ssSamplingSteps", 12)),
            "slat_guidance_strength": float(
                request.parameters.get("slatGuidanceStrength", 3.0)
            ),
            "slat_sampling_steps": int(request.parameters.get("slatSamplingSteps", 12)),
            "multiimage_algo": "stochastic",
            "mesh_simplify": float(request.parameters.get("meshSimplify", 0.95)),
            "texture_size": int(request.parameters.get("textureSize", 512)),
        }
        client.predict(api_name="/start_session")
        result = client.predict(
            image=primary,
            multiimages=extra,
            api_name="/generate_and_extract_glb",
            **parameters,
        )
        source = _find_glb(result)
        if source is None:
            raise AssistFailure(
                "invalid_provider_response",
                "TRELLIS response contains no downloaded GLB",
            )
        return RawGeneration(
            source,
            model_id=spec.model_id,
            metadata={"apiName": "/generate_and_extract_glb"},
        )


class Sf3dZeroGpuProvider:
    def generate(self, request: GenerationRequest, run_dir: Path) -> RawGeneration:
        if len(request.images) != 1:
            raise AssistFailure(
                "invalid_provider_response",
                "hosted Stable Fast 3D accepts exactly one input image",
            )
        try:
            from gradio_client import Client, handle_file
        except ImportError as exc:
            raise AssistFailure(
                "provider_unavailable",
                "gradio_client is required for hosted generation",
            ) from exc
        spec = provider_spec(request.provider_id)
        client = Client(spec.endpoint, token=_token(), verbose=False)
        result = client.predict(
            input_image=handle_file(str(request.images[0].resolve())),
            foreground_ratio=float(request.parameters.get("foregroundRatio", 0.85)),
            remesh_option=str(request.parameters.get("remeshOption", "None")),
            vertex_count=int(request.parameters.get("vertexCount", -1)),
            texture_size=int(request.parameters.get("textureSize", 1024)),
            api_name="/run_button",
        )
        source = _find_glb(result)
        if source is None:
            raise AssistFailure(
                "invalid_provider_response",
                "Stable Fast 3D response contains no downloaded GLB",
            )
        return RawGeneration(
            source, model_id=spec.model_id, metadata={"apiName": "/run_button"}
        )


class LocalSf3dProvider:
    def generate(self, request: GenerationRequest, run_dir: Path) -> RawGeneration:
        root_value = os.environ.get("SF3D_ROOT", "")
        root = (
            Path(root_value).expanduser().resolve()
            if root_value
            else Path("/__missing_sf3d_root__")
        )
        script = root / "run.py"
        if not script.is_file():
            raise AssistFailure(
                "local_capability_missing", "SF3D_ROOT/run.py is missing"
            )
        output = run_dir / "local-provider-output"
        output.mkdir(parents=True, exist_ok=False)
        command = [
            sys.executable,
            str(script),
            *(str(path.resolve()) for path in request.images),
            "--output-dir",
            str(output),
        ]
        environment = os.environ.copy()
        if bool(request.parameters.get("forceCpu", False)):
            environment["SF3D_USE_CPU"] = "1"
        completed = subprocess.run(
            command,
            cwd=root,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise AssistFailure(
                "provider_unavailable",
                f"local Stable Fast 3D exited {completed.returncode}: {completed.stderr[-1000:]}",
            )
        candidates = sorted(output.glob("**/*.glb"))
        if len(candidates) != 1:
            raise AssistFailure(
                "invalid_provider_response",
                f"local Stable Fast 3D produced {len(candidates)} GLB files",
            )
        spec = provider_spec(request.provider_id)
        return RawGeneration(
            candidates[0],
            model_id=spec.model_id,
            metadata={
                "command": [
                    "python",
                    "run.py",
                    "<images>",
                    "--output-dir",
                    "<run-output>",
                ]
            },
        )


def provider_adapter(provider_id: str) -> MeshProvider:
    if provider_id == "hf-zerogpu-trellis":
        return TrellisZeroGpuProvider()
    if provider_id == "hf-zerogpu-sf3d":
        return Sf3dZeroGpuProvider()
    if provider_id == "local-sf3d":
        return LocalSf3dProvider()
    provider_spec(provider_id)
    raise AssertionError("unreachable")
