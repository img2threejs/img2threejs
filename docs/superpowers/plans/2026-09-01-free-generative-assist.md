> Last updated: 2026-09-01 00:25

# Free Generative Assist Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in, zero-spend, approval-gated and resumable reference-mesh generator for hosted TRELLIS, hosted Stable Fast 3D, and a separately installed local Stable Fast 3D checkout.

**Architecture:** Keep provider dependencies isolated in `integrations/mesh3d/free_assist/`; the package has immutable policy/registry data, injected live probes, provider adapters, durable artifacts, cache lookup, normalization, and admission. The CLI performs a read-only preflight, re-runs that preflight before a single approved generation, and resumes only from an already persisted raw GLB. The stdlib `forge/` core remains unchanged and receives only normalized GLB/OBJ/report artifacts.

**Tech Stack:** Python 3.10+ stdlib for policy/orchestration/tests; optional `huggingface_hub`, `gradio_client`, and `trimesh` at provider/normalization boundaries; `unittest` fixtures; Hugging Face Space metadata API.

---

## File map

- `integrations/mesh3d/free_assist/model.py`: enums, immutable zero-cost policy, request/preflight/run records, JSON helpers.
- `integrations/mesh3d/free_assist/registry.py`: the only reviewed provider/endpoint allowlist and live ZeroGPU admission.
- `integrations/mesh3d/free_assist/security.py`: token/signed-URL redaction and safe atomic JSON writes.
- `integrations/mesh3d/free_assist/providers.py`: TRELLIS, hosted SF3D, and already-installed local SF3D adapters; optional imports occur only on generation.
- `integrations/mesh3d/free_assist/pipeline.py`: hashing/cache, preflight, one-shot generation, raw recovery boundary, normalization, and mesh admission.
- `integrations/mesh3d/free_assist/cli.py`, `__main__.py`: `preflight`, `generate`, and `resume` commands with no token argument.
- `forge/tests/test_free_generative_assist.py`: offline behavior and fixture integration tests; no provider calls.
- `docs/integrations/free-generative-assist.md`: safety model, usage, prerequisites, and exclusions.
- `README.md`, `SKILL.md`, `ROADMAP.md`, `CHANGELOG.md`: honest discoverability and capability status.

### Task 1: Immutable policy, provider registry, and redaction

**Files:**
- Create: `integrations/mesh3d/__init__.py`
- Create: `integrations/mesh3d/free_assist/__init__.py`
- Create: `integrations/mesh3d/free_assist/model.py`
- Create: `integrations/mesh3d/free_assist/registry.py`
- Create: `integrations/mesh3d/free_assist/security.py`
- Test: `forge/tests/test_free_generative_assist.py`

- [ ] **Step 1: Write failing policy and registry tests**

```python
def test_policy_is_immutable_and_zero_cost(self):
    self.assertEqual(ZeroSpendPolicy().max_cost_usd, 0)
    with self.assertRaises(dataclasses.FrozenInstanceError):
        ZeroSpendPolicy().max_cost_usd = 1

def test_unknown_or_non_zero_hardware_fails_closed(self):
    request = make_request("hf-zerogpu-trellis")
    self.assertEqual(preflight(request, metadata={"hardware": "a10g"}).decision, Decision.DENY)
    with self.assertRaises(UnknownProvider):
        provider_spec("unknown")
```

- [ ] **Step 2: Verify RED**

Run: `python3 -m unittest forge.tests.test_free_generative_assist.PolicyRegistryTest -v`
Expected: `ModuleNotFoundError: integrations.mesh3d.free_assist`.

- [ ] **Step 3: Implement the immutable contract and exact allowlist**

```python
@dataclass(frozen=True)
class ZeroSpendPolicy:
    max_cost_usd: int = 0
    allow_paid_fallback: bool = False
    allow_credit_purchase: bool = False
    allow_automatic_retry: bool = False
    allow_automatic_provider_switch: bool = False

PROVIDERS = {
    "hf-zerogpu-trellis": ProviderSpec("trellis-community/TRELLIS", "zero-a10g", True),
    "hf-zerogpu-sf3d": ProviderSpec("stabilityai/stable-fast-3d", "zero-a10g", True),
    "local-sf3d": ProviderSpec("local", "local", False),
}
```

Admission accepts hosted providers only when `runtime.stage == RUNNING`, a runtime domain is `READY`, and both current/requested hardware equal `zero-a10g`; missing fields return `DENY`. Redaction recursively removes bearer/HF tokens, cookies, authorization values, and sensitive URL query values before any exception/report is serialized.

- [ ] **Step 4: Verify GREEN**

Run: `python3 -m unittest forge.tests.test_free_generative_assist.PolicyRegistryTest -v`
Expected: policy, unknown-provider, hardware, and redaction tests pass.

### Task 2: Content-addressed requests and read-only preflight

**Files:**
- Create: `integrations/mesh3d/free_assist/pipeline.py`
- Modify: `forge/tests/test_free_generative_assist.py`

- [ ] **Step 1: Write failing cache-key and preflight tests**

```python
def test_cache_key_changes_for_inputs_provider_revision_and_parameters(self):
    base = request_for(self.image)
    keys = {compute_cache_key(replace(base, seed=n)) for n in (0, 1)}
    self.assertEqual(len(keys), 2)

def test_preflight_does_not_construct_or_call_provider(self):
    probe = RecordingMetadataProbe(ZERO_GPU_METADATA)
    report = preflight(request_for(self.image), metadata_probe=probe)
    self.assertEqual(report.decision, Decision.NEEDS_USER_ACTION)
    self.assertEqual(probe.calls, ["trellis-community/TRELLIS"])
    self.assertFalse(self.upload.called)
```

- [ ] **Step 2: Verify RED**

Run: `python3 -m unittest forge.tests.test_free_generative_assist.RequestPreflightTest -v`
Expected: imports for `compute_cache_key` and `preflight` fail.

- [ ] **Step 3: Implement canonical hashing and preflight**

```python
def compute_cache_key(request: GenerationRequest) -> str:
    payload = {"images": [sha256_file(p) for p in request.images],
               "providerId": request.provider_id,
               "endpointRevision": request.endpoint_revision,
               "parameters": request.parameters,
               "normalizerVersion": NORMALIZER_VERSION}
    return hashlib.sha256(canonical_json(payload)).hexdigest()
```

`preflight` validates files, probes only metadata/capability, checks cache before network generation, writes `preflight.json` atomically, and returns `NEEDS_USER_ACTION` for an admitted hosted request until exact upload approval is supplied. Local preflight checks platform, `SF3D_ROOT/run.py`, disk, memory, token availability, and separately reported license/install/run approvals; it never installs or accepts terms.

- [ ] **Step 4: Verify GREEN**

Run: `python3 -m unittest forge.tests.test_free_generative_assist.RequestPreflightTest -v`
Expected: all request, hashing, cache, and no-upload tests pass.

### Task 3: One-shot provider adapters and durable raw GLB

**Files:**
- Create: `integrations/mesh3d/free_assist/providers.py`
- Modify: `integrations/mesh3d/free_assist/pipeline.py`
- Modify: `forge/tests/test_free_generative_assist.py`

- [ ] **Step 1: Write failing one-call, approval, and no-fallback tests**

```python
def test_generate_requires_exact_upload_approval(self):
    with self.assertRaises(AssistFailure) as error:
        generate(self.request, approve_upload=False, adapter=self.adapter)
    self.assertEqual(error.exception.category, "upload_not_approved")
    self.assertEqual(self.adapter.calls, 0)

def test_provider_failure_is_not_retried_or_switched(self):
    self.adapter.failure = RuntimeError("queue failed")
    with self.assertRaises(AssistFailure):
        generate(self.request, approve_upload=True, adapter=self.adapter)
    self.assertEqual(self.adapter.calls, 1)
```

- [ ] **Step 2: Verify RED**

Run: `python3 -m unittest forge.tests.test_free_generative_assist.GenerationTest -v`
Expected: generation entrypoint is missing.

- [ ] **Step 3: Implement adapters and durable write boundary**

TRELLIS calls `/start_session` then exactly one `/generate_and_extract_glb`; hosted SF3D calls exactly one `/run_button`; both discover credentials only through `huggingface_hub.get_token()`/`HF_TOKEN`. The local adapter runs `<SF3D_ROOT>/run.py` once through `sys.executable` after explicit local approval. Copy the returned GLB to `runs/<id>/raw/reference.glb.tmp`, verify GLB magic, rename atomically, hash it, then write a redacted receipt/status. No adapter knows about or invokes another adapter.

- [ ] **Step 4: Verify GREEN**

Run: `python3 -m unittest forge.tests.test_free_generative_assist.GenerationTest -v`
Expected: approval, one-call, response extraction, redaction, and no-fallback tests pass.

### Task 4: Resume, normalization, cache, and admission

**Files:**
- Modify: `integrations/mesh3d/free_assist/pipeline.py`
- Modify: `forge/tests/test_free_generative_assist.py`

- [ ] **Step 1: Write failing fixture integration tests**

```python
def test_normalization_failure_resumes_raw_without_generation(self):
    run = make_run_with_triangle_glb(self.root)
    first = resume(run, obj_writer=raising_writer)
    second = resume(run, obj_writer=fixture_obj_writer)
    self.assertEqual(first["failureCategory"], "normalization_failed")
    self.assertEqual(second["status"], "complete")
    self.assertEqual(self.adapter.calls, 0)

def test_invalid_glb_and_degenerate_bounds_fail_admission(self):
    self.assertEqual(admit_glb(self.bad_glb)["failureCategory"], "invalid_glb")
    self.assertEqual(admit_glb(self.flat_glb)["failureCategory"], "mesh_admission_failed")
```

- [ ] **Step 2: Verify RED**

Run: `python3 -m unittest forge.tests.test_free_generative_assist.ResumeAdmissionTest -v`
Expected: resume/admission functions are missing.

- [ ] **Step 3: Implement normalization and admission**

Reuse `forge.stage1_intake.probe_glb.probe_glb` and the existing OBJ conversion helper. Admission requires readable GLB v2, mesh/material/BIN inventory, finite non-zero bounds, and configurable vertex/triangle ceilings; it records compression and OBJ availability. Resume reads only `raw/reference.glb`, verifies its recorded hash, recreates normalized artifacts and reports, and never obtains a provider adapter. On success update `cache-index.json` atomically; a matching later request returns the existing completed run without network access.

- [ ] **Step 4: Verify GREEN**

Run: `python3 -m unittest forge.tests.test_free_generative_assist.ResumeAdmissionTest -v`
Expected: TRELLIS/SF3D-shaped fixture responses, invalid GLB, resume, and cache tests pass.

### Task 5: Safe CLI contract

**Files:**
- Create: `integrations/mesh3d/free_assist/cli.py`
- Create: `integrations/mesh3d/free_assist/__main__.py`
- Modify: `forge/tests/test_free_generative_assist.py`

- [ ] **Step 1: Write failing CLI tests**

```python
def test_help_has_no_token_or_spend_override(self):
    result = run_cli("--help")
    self.assertNotIn("--hf-token", result.stdout)
    self.assertNotIn("max-cost", result.stdout)

def test_generate_without_approval_never_calls_provider(self):
    result = run_cli("generate", str(self.image), "--provider", "hf-zerogpu-trellis",
                     "--out-dir", str(self.root))
    self.assertEqual(result.returncode, 3)
```

- [ ] **Step 2: Verify RED**

Run: `python3 -m unittest forge.tests.test_free_generative_assist.CliTest -v`
Expected: module CLI is missing.

- [ ] **Step 3: Implement `preflight`, `generate`, and `resume`**

The parser exposes exact provider choices, image paths, output/run path, reviewed generation parameters, `--approve-upload`, and `--approve-local-run`; it exposes no endpoint override, token, paid fallback, retry, or cost override. JSON is the default report format; exit codes are `0` complete/cache hit, `2` invalid invocation/local error, and `3` policy/user-action denial.

- [ ] **Step 4: Verify GREEN**

Run: `python3 -m unittest forge.tests.test_free_generative_assist.CliTest -v`
Expected: CLI safety and fixture-driven subprocess tests pass without network.

### Task 6: Documentation and complete verification

**Files:**
- Create: `docs/integrations/free-generative-assist.md`
- Modify: `README.md`
- Modify: `SKILL.md`
- Modify: `ROADMAP.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add a failing documentation contract test**

```python
def test_docs_state_free_limits_and_manual_live_gate(self):
    text = DOC.read_text()
    for phrase in ("maxCostUsd = 0", "ZeroGPU", "--approve-upload", "resume",
                   "no automatic retry", "separate live acceptance"):
        self.assertIn(phrase, text)
```

- [ ] **Step 2: Verify RED**

Run: `python3 -m unittest forge.tests.test_free_generative_assist.DocumentationTest -v`
Expected: missing documentation file.

- [ ] **Step 3: Document the implemented boundary**

Explain current quota/queue data as live preflight evidence rather than a permanent number; token login; exact upload review; hosted/provider visibility and licenses; local SF3D gated access, MPS/CPU/disk/memory prerequisites; why Tripo/Meshy are manual/excluded; resume/cache; generated mesh as proxy; and the separately approved one-run live test. Add release-facing links and mark the live acceptance as deliberately not yet executed.

- [ ] **Step 4: Run focused and full verification**

Run: `python3 -m unittest forge.tests.test_free_generative_assist -v`
Expected: all free-assist tests pass with no network.

Run: `python3 -m unittest discover -s forge/tests -p 'test_*.py'`
Expected: baseline 1,083 tests plus new tests pass; optional showcase tests may remain skipped.

Run: `python3 -m integrations.mesh3d.free_assist --help && git diff --check && rg -n "hf_[A-Za-z0-9]{20,}|Bearer [A-Za-z0-9]" integrations/mesh3d/free_assist forge/tests/test_free_generative_assist.py docs/integrations/free-generative-assist.md`
Expected: CLI help succeeds, diff check is clean, and credential scan has no matches.

The separately approved live TRELLIS ZeroGPU acceptance run is not executed in this plan.
