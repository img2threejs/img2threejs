from __future__ import annotations

import dataclasses
import io
import json
import os
import struct
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from integrations.mesh3d.free_assist.model import (
    AssistFailure,
    Decision,
    GenerationRequest,
    RawGeneration,
    ZeroSpendPolicy,
)
from integrations.mesh3d.free_assist.pipeline import (
    admit_glb,
    compute_cache_key,
    default_metadata_probe,
    generate,
    local_capability,
    local_install_revision,
    normalize_provider_failure,
    preflight,
    resume,
    sha256_file,
)
from integrations.mesh3d.free_assist.registry import (
    MetadataDecision,
    UnknownProvider,
    evaluate_hosted_metadata,
    provider_spec,
)
from integrations.mesh3d.free_assist.security import redact
from integrations.mesh3d.free_assist.cli import build_parser, main as cli_main


def write_triangle_glb(
    path: Path, *, degenerate: bool = False, with_material: bool = True
) -> None:
    positions = struct.pack(
        "<9f",
        0.0,
        0.0,
        0.0,
        0.0 if degenerate else 1.0,
        0.0,
        0.0,
        0.0,
        0.0 if degenerate else 1.0,
        0.0,
    )
    document = {
        "asset": {"version": "2.0", "generator": "free-assist-test"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"name": "Root", "mesh": 0}],
        "meshes": [
            {
                "name": "Triangle",
                "primitives": [{"attributes": {"POSITION": 0}, "material": 0}],
            }
        ],
        "materials": [{"name": "Fixture"}] if with_material else [],
        "buffers": [{"byteLength": len(positions)}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(positions)}],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": 3,
                "type": "VEC3",
                "min": [0.0, 0.0, 0.0],
                "max": [0.0, 0.0, 0.0] if degenerate else [1.0, 1.0, 0.0],
            }
        ],
    }
    json_chunk = json.dumps(document, separators=(",", ":")).encode()
    json_chunk += b" " * ((4 - len(json_chunk) % 4) % 4)
    binary = positions + b"\0" * ((4 - len(positions) % 4) % 4)
    total = 12 + 8 + len(json_chunk) + 8 + len(binary)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        struct.pack("<4sII", b"glTF", 2, total)
        + struct.pack("<II", len(json_chunk), 0x4E4F534A)
        + json_chunk
        + struct.pack("<II", len(binary), 0x004E4942)
        + binary
    )


class PolicyRegistryTest(unittest.TestCase):
    def test_policy_is_immutable_and_zero_cost(self) -> None:
        policy = ZeroSpendPolicy()
        self.assertEqual(policy.max_cost_usd, 0)
        self.assertFalse(policy.allow_paid_fallback)
        self.assertFalse(policy.allow_credit_purchase)
        self.assertFalse(policy.allow_automatic_retry)
        self.assertFalse(policy.allow_automatic_provider_switch)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            policy.max_cost_usd = 1  # type: ignore[misc]

    def test_registry_contains_only_reviewed_providers(self) -> None:
        self.assertEqual(
            provider_spec("hf-zerogpu-trellis").endpoint, "trellis-community/TRELLIS"
        )
        self.assertEqual(
            provider_spec("hf-zerogpu-sf3d").endpoint, "stabilityai/stable-fast-3d"
        )
        self.assertEqual(provider_spec("local-sf3d").endpoint, "local")
        with self.assertRaises(UnknownProvider):
            provider_spec("unknown")

    def test_unknown_or_non_zero_hardware_fails_closed(self) -> None:
        good = {
            "runtime": {
                "stage": "RUNNING",
                "hardware": {"current": "zero-a10g", "requested": "zero-a10g"},
                "domains": [{"domain": "example.hf.space", "stage": "READY"}],
                "sha": "abc123",
            }
        }
        self.assertEqual(evaluate_hosted_metadata(good).decision, Decision.ALLOW)
        for metadata in (
            {},
            {
                "runtime": {
                    "stage": "RUNNING",
                    "hardware": {"current": "a10g", "requested": "a10g"},
                }
            },
            {
                "runtime": {
                    "stage": "STOPPED",
                    "hardware": {"current": "zero-a10g", "requested": "zero-a10g"},
                }
            },
        ):
            self.assertEqual(evaluate_hosted_metadata(metadata).decision, Decision.DENY)

    def test_redaction_removes_tokens_headers_cookies_and_signed_queries(self) -> None:
        value = {
            "Authorization": "Bearer hf_TEST-TOKEN-abcdefghijklmnopqrstuvwxyz",
            "cookie": "session=secret",
            "url": "https://host/file?token=secret&download=1&X-Amz-Signature=abc",
            "message": "failed with hf_TEST-TOKEN-abcdefghijklmnopqrstuvwxyz",
            "generic": "request contained Bearer very-secret-provider-token",
            "safe": "visible",
        }
        result = redact(value)
        serialized = repr(result)
        self.assertNotIn("secret", serialized)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", serialized)
        self.assertNotIn("very-secret-provider-token", serialized)
        self.assertNotIn("X-Amz-Signature", serialized)
        self.assertEqual(result["safe"], "visible")


class RequestPreflightTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.image = self.root / "front.png"
        self.image.write_bytes(b"fixture-image")
        self.metadata = {
            "runtime": {
                "stage": "RUNNING",
                "hardware": {"current": "zero-a10g", "requested": "zero-a10g"},
                "domains": [
                    {"domain": "trellis-community-trellis.hf.space", "stage": "READY"}
                ],
                "sha": "reviewed-revision",
            }
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def request(self, **parameters: object) -> GenerationRequest:
        return GenerationRequest(
            images=(self.image,),
            provider_id="hf-zerogpu-trellis",
            out_dir=self.root / "artifacts",
            endpoint_revision="reviewed-revision",
            parameters=dict(parameters),
        )

    def test_cache_key_changes_for_input_provider_revision_and_parameters(self) -> None:
        base = self.request(seed=0)
        variants = (
            base,
            replace(base, provider_id="hf-zerogpu-sf3d"),
            replace(base, endpoint_revision="another-revision"),
            replace(base, parameters={"seed": 1}),
        )
        self.assertEqual(
            len({compute_cache_key(item) for item in variants}), len(variants)
        )
        self.image.write_bytes(b"different-image")
        self.assertNotEqual(
            compute_cache_key(base),
            compute_cache_key(replace(base, parameters={"seed": 1})),
        )

    def test_preflight_is_metadata_only_and_requires_upload_approval(self) -> None:
        calls: list[str] = []

        def probe(endpoint: str) -> dict:
            calls.append(endpoint)
            return self.metadata

        report = preflight(self.request(seed=0), metadata_probe=probe)
        self.assertEqual(report.decision, Decision.NEEDS_USER_ACTION)
        self.assertEqual(calls, ["trellis-community/TRELLIS"])
        self.assertEqual(report.upload_files, (str(self.image.resolve()),))
        saved = json.loads((self.root / "artifacts" / "preflight.json").read_text())
        self.assertEqual(saved["decision"], "NEEDS_USER_ACTION")

    def test_preflight_denies_non_zero_hardware(self) -> None:
        metadata = dict(self.metadata)
        metadata["runtime"] = dict(self.metadata["runtime"])
        metadata["runtime"]["hardware"] = {"current": "a10g", "requested": "a10g"}
        report = preflight(
            self.request(),
            metadata_probe=lambda _endpoint: metadata,
            approve_upload=True,
        )
        self.assertEqual(report.decision, Decision.DENY)

    def test_metadata_probe_failure_writes_fail_closed_preflight_report(self) -> None:
        def failing_probe(_endpoint: str) -> dict:
            raise RuntimeError("metadata network unavailable")

        report = preflight(
            self.request(), metadata_probe=failing_probe, approve_upload=True
        )
        self.assertEqual(report.decision, Decision.DENY)
        self.assertEqual(report.evidence["failureCategory"], "free_status_unverified")
        saved = json.loads((self.root / "artifacts" / "preflight.json").read_text())
        self.assertEqual(saved["decision"], "DENY")

    def test_huggingface_space_runtime_object_uses_raw_metadata(self) -> None:
        card = SimpleNamespace(to_dict=lambda: {"license": "mit"})
        info = SimpleNamespace(
            id="trellis-community/TRELLIS",
            runtime=SimpleNamespace(raw=self.metadata["runtime"]),
            card_data=card,
        )
        with patch("huggingface_hub.HfApi") as api:
            api.return_value.space_info.return_value = info
            result = default_metadata_probe("trellis-community/TRELLIS")
        self.assertEqual(result["runtime"]["hardware"]["current"], "zero-a10g")
        self.assertEqual(result["cardData"]["license"], "mit")

    def test_local_capability_enforces_disk_and_memory_and_selects_safe_backend(
        self,
    ) -> None:
        low_disk = local_capability(
            self.root,
            system="Darwin",
            machine="arm64",
            disk_free_bytes=8 << 30,
            memory_bytes=64 << 30,
        )
        self.assertEqual(low_disk.decision, Decision.UNAVAILABLE)
        low_memory = local_capability(
            self.root,
            system="Darwin",
            machine="arm64",
            disk_free_bytes=20 << 30,
            memory_bytes=8 << 30,
        )
        self.assertEqual(low_memory.decision, Decision.UNAVAILABLE)
        cpu_safe = local_capability(
            self.root,
            system="Darwin",
            machine="arm64",
            disk_free_bytes=20 << 30,
            memory_bytes=16 << 30,
        )
        self.assertEqual(cpu_safe.decision, Decision.ALLOW)
        self.assertEqual(cpu_safe.evidence["recommendedBackend"], "cpu")
        mps = local_capability(
            self.root,
            system="Darwin",
            machine="arm64",
            disk_free_bytes=20 << 30,
            memory_bytes=64 << 30,
        )
        self.assertEqual(mps.evidence["recommendedBackend"], "mps-experimental")

    def test_local_install_revision_changes_with_fallback_script_content(self) -> None:
        root = self.root / "sf3d-revision"
        root.mkdir()
        script = root / "run.py"
        script.write_text("first\n", encoding="utf-8")
        first = local_install_revision(root)
        script.write_text("second\n", encoding="utf-8")
        second = local_install_revision(root)
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("run-py-sha256:"))


class _FakeAdapter:
    def __init__(self, source: Path, failure: Exception | None = None) -> None:
        self.source = source
        self.failure = failure
        self.calls = 0

    def generate(self, request: GenerationRequest, run_dir: Path) -> RawGeneration:
        self.calls += 1
        if self.failure:
            raise self.failure
        return RawGeneration(
            self.source, provider_task_id="fixture-task", model_id="fixture-model"
        )


class _CapturingAdapter(_FakeAdapter):
    captured_request: GenerationRequest | None = None

    def generate(self, request: GenerationRequest, run_dir: Path) -> RawGeneration:
        self.captured_request = request
        return super().generate(request, run_dir)


class GenerationTest(RequestPreflightTest):
    def setUp(self) -> None:
        super().setUp()
        self.source = self.root / "provider-output.glb"
        write_triangle_glb(self.source)

    def test_generate_requires_exact_upload_approval(self) -> None:
        adapter = _FakeAdapter(self.source)
        with self.assertRaises(AssistFailure) as error:
            generate(
                self.request(seed=0),
                approve_upload=False,
                adapter=adapter,
                metadata_probe=lambda _: self.metadata,
            )
        self.assertEqual(error.exception.category, "upload_not_approved")
        self.assertEqual(adapter.calls, 0)

    def test_provider_failure_is_not_retried_or_switched(self) -> None:
        adapter = _FakeAdapter(
            self.source,
            RuntimeError("queue failed with hf_TEST-TOKEN-abcdefghijklmnopqrstuvwxyz"),
        )
        with self.assertRaises(AssistFailure) as error:
            generate(
                self.request(seed=0),
                approve_upload=True,
                adapter=adapter,
                metadata_probe=lambda _: self.metadata,
            )
        self.assertEqual(error.exception.category, "provider_unavailable")
        self.assertEqual(adapter.calls, 1)
        statuses = list((self.root / "artifacts" / "runs").glob("*/status.json"))
        self.assertEqual(len(statuses), 1)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", statuses[0].read_text())

    def test_adapter_assist_failure_is_redacted_before_rethrow(self) -> None:
        adapter = _FakeAdapter(
            self.source,
            AssistFailure(
                "authentication_required",
                "rejected hf_TEST-TOKEN-abcdefghijklmnopqrstuvwxyz",
            ),
        )
        with self.assertRaises(AssistFailure) as error:
            generate(
                self.request(),
                approve_upload=True,
                adapter=adapter,
                metadata_probe=lambda _: self.metadata,
            )
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", str(error.exception))
        self.assertEqual(error.exception.category, "authentication_required")

    def test_provider_failures_use_normalized_queue_and_quota_categories(self) -> None:
        self.assertEqual(
            normalize_provider_failure(TimeoutError("timed out")), "queue_timeout"
        )
        self.assertEqual(
            normalize_provider_failure(RuntimeError("ZeroGPU quota exceeded")),
            "quota_exhausted",
        )
        self.assertEqual(
            normalize_provider_failure(RuntimeError("service unavailable")),
            "provider_unavailable",
        )

    def test_success_persists_immutable_raw_glb_and_receipt(self) -> None:
        adapter = _FakeAdapter(self.source)
        run = generate(
            self.request(seed=7),
            approve_upload=True,
            adapter=adapter,
            metadata_probe=lambda _: self.metadata,
        )
        raw = run / "raw" / "reference.glb"
        self.assertEqual(adapter.calls, 1)
        self.assertTrue(raw.is_file())
        self.assertEqual(raw.read_bytes()[:4], b"glTF")
        receipt = json.loads((run / "provider-receipt.json").read_text())
        self.assertEqual(receipt["declaredMonetaryCostUsd"], 0)
        self.assertEqual(receipt["rawSha256"], sha256_file(raw))

    def test_local_cpu_recommendation_is_enforced_on_provider_request(self) -> None:
        sf3d_root = self.root / "sf3d"
        sf3d_root.mkdir()
        (sf3d_root / "run.py").write_text("# fixture\n", encoding="utf-8")
        request = replace(self.request(seed=0), provider_id="local-sf3d")
        adapter = _CapturingAdapter(self.source)
        capability = MetadataDecision(Decision.ALLOW, (), {"recommendedBackend": "cpu"})
        with (
            patch.dict(
                os.environ,
                {"SF3D_ROOT": str(sf3d_root), "SF3D_MODEL_ACCESS_APPROVED": "1"},
            ),
            patch(
                "integrations.mesh3d.free_assist.pipeline.local_capability",
                return_value=capability,
            ),
        ):
            generate(request, approve_local_run=True, adapter=adapter)
        self.assertIsNotNone(adapter.captured_request)
        self.assertTrue(adapter.captured_request.parameters["forceCpu"])

    def test_local_missing_user_license_acceptance_has_specific_failure(self) -> None:
        sf3d_root = self.root / "sf3d-no-license"
        sf3d_root.mkdir()
        (sf3d_root / "run.py").write_text("# fixture\n", encoding="utf-8")
        request = replace(self.request(seed=0), provider_id="local-sf3d")
        capability = MetadataDecision(Decision.ALLOW, (), {"recommendedBackend": "cpu"})
        environment = {
            key: value
            for key, value in os.environ.items()
            if key != "SF3D_MODEL_ACCESS_APPROVED"
        }
        environment["SF3D_ROOT"] = str(sf3d_root)
        with (
            patch.dict(os.environ, environment, clear=True),
            patch(
                "integrations.mesh3d.free_assist.pipeline.local_capability",
                return_value=capability,
            ),
        ):
            with self.assertRaises(AssistFailure) as error:
                generate(
                    request, approve_local_run=True, adapter=_FakeAdapter(self.source)
                )
        self.assertEqual(error.exception.category, "license_acceptance_required")


class ResumeAdmissionTest(GenerationTest):
    @staticmethod
    def fixture_obj_writer(_glb: Path, obj: Path) -> dict:
        obj.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n", encoding="utf-8")
        return {
            "vertices": 3,
            "triangles": 1,
            "bbox_min": [0.0, 0.0, 0.0],
            "bbox_max": [1.0, 1.0, 0.0],
            "size": [1.0, 1.0, 0.0],
            "watertight": False,
        }

    def raw_run(self) -> tuple[Path, _FakeAdapter]:
        adapter = _FakeAdapter(self.source)
        run = generate(
            self.request(seed=7),
            approve_upload=True,
            adapter=adapter,
            metadata_probe=lambda _: self.metadata,
        )
        return run, adapter

    def test_normalization_failure_resumes_raw_without_generation(self) -> None:
        run, adapter = self.raw_run()

        def raising_writer(_glb: Path, _obj: Path) -> dict:
            raise RuntimeError("local converter failed")

        first = resume(run, obj_writer=raising_writer)
        self.assertEqual(first["failureCategory"], "normalization_failed")
        self.assertTrue(first["resumable"])
        second = resume(run, obj_writer=self.fixture_obj_writer)
        self.assertEqual(second["status"], "complete")
        self.assertEqual(adapter.calls, 1)
        self.assertTrue((run / "normalized" / "reference.obj").is_file())

    def test_invalid_glb_and_degenerate_bounds_fail_admission(self) -> None:
        bad = self.root / "bad.glb"
        bad.write_bytes(b"not-a-glb")
        bad_report = admit_glb(bad)
        self.assertEqual(bad_report["failureCategory"], "invalid_glb")

        flat = self.root / "degenerate.glb"
        write_triangle_glb(flat, degenerate=True)
        flat_report = admit_glb(flat)
        self.assertEqual(flat_report["failureCategory"], "mesh_admission_failed")

    def test_completed_cache_prevents_second_provider_call(self) -> None:
        run, first_adapter = self.raw_run()
        completed = resume(run, obj_writer=self.fixture_obj_writer)
        self.assertEqual(completed["status"], "complete")
        second_adapter = _FakeAdapter(self.source)

        def network_forbidden(_endpoint: str) -> dict:
            raise AssertionError("cache reuse must not probe the network")

        cached = generate(
            self.request(seed=7),
            approve_upload=False,
            adapter=second_adapter,
            metadata_probe=network_forbidden,
        )
        self.assertEqual(cached, run)
        self.assertEqual(first_adapter.calls, 1)
        self.assertEqual(second_adapter.calls, 0)

    def test_completed_local_cache_bypasses_install_and_license_checks(self) -> None:
        sf3d_root = self.root / "sf3d"
        sf3d_root.mkdir()
        (sf3d_root / "run.py").write_text("# fixture\n", encoding="utf-8")
        request = replace(self.request(seed=3), provider_id="local-sf3d")
        capability = MetadataDecision(Decision.ALLOW, (), {"recommendedBackend": "cpu"})
        with (
            patch.dict(
                os.environ,
                {"SF3D_ROOT": str(sf3d_root), "SF3D_MODEL_ACCESS_APPROVED": "1"},
            ),
            patch(
                "integrations.mesh3d.free_assist.pipeline.local_capability",
                return_value=capability,
            ),
        ):
            run = generate(
                request, approve_local_run=True, adapter=_FakeAdapter(self.source)
            )
        self.assertEqual(
            resume(run, obj_writer=self.fixture_obj_writer)["status"], "complete"
        )
        with patch.dict(os.environ, {}, clear=True):
            cached = generate(request, adapter=_FakeAdapter(self.root / "missing.glb"))
        self.assertEqual(cached, run)


class CliTest(RequestPreflightTest):
    def test_help_has_no_token_spend_retry_or_endpoint_override(self) -> None:
        help_text = build_parser().format_help()
        for forbidden in (
            "--hf-token",
            "--token",
            "max-cost",
            "paid-fallback",
            "--retry",
            "--space",
        ):
            self.assertNotIn(forbidden, help_text)

    def test_generate_without_approval_never_constructs_provider(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli_main(
                [
                    "generate",
                    str(self.image),
                    "--provider",
                    "hf-zerogpu-trellis",
                    "--out-dir",
                    str(self.root / "cli-artifacts"),
                ],
                metadata_probe=lambda _endpoint: self.metadata,
            )
        self.assertEqual(code, 3)
        self.assertIn("upload_not_approved", stderr.getvalue())
        self.assertFalse((self.root / "cli-artifacts" / "runs").exists())

    def test_preflight_prints_machine_readable_decision(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli_main(
                [
                    "preflight",
                    str(self.image),
                    "--provider",
                    "hf-zerogpu-trellis",
                    "--out-dir",
                    str(self.root / "cli-artifacts"),
                ],
                metadata_probe=lambda _endpoint: self.metadata,
            )
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(stdout.getvalue())["decision"], "NEEDS_USER_ACTION")


class DocumentationTest(unittest.TestCase):
    DOC = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "integrations"
        / "free-generative-assist.md"
    )
    PROJECT = (
        Path(__file__).resolve().parents[2]
        / "integrations"
        / "mesh3d"
        / "pyproject.toml"
    )

    def test_docs_state_free_limits_and_manual_live_gate(self) -> None:
        text = self.DOC.read_text(encoding="utf-8")
        for phrase in (
            "maxCostUsd = 0",
            "ZeroGPU",
            "--approve-upload",
            "resume",
            "no automatic retry",
            "separate live acceptance",
            "Tripo",
            "Meshy",
            "SF3D_MODEL_ACCESS_APPROVED",
        ):
            self.assertIn(phrase, text)
        self.assertTrue(text.startswith("> Last updated: 2026-09-01"))

    def test_optional_integration_declares_provider_dependencies(self) -> None:
        text = self.PROJECT.read_text(encoding="utf-8")
        for package in ("gradio-client", "huggingface-hub", "trimesh", "scipy"):
            self.assertIn(package, text)


if __name__ == "__main__":
    unittest.main()
