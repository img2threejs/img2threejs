> Last updated: 2026-09-01 00:15

# Free Generative Assist Specification

## 1. Summary

Add an explicit, opt-in `generativeAssist` path to img2threejs that can obtain a generated reference mesh without monetary spend. The feature extends the existing hosted TRELLIS proof of concept into a provider-neutral, resumable, quality-gated workflow.

The first implementation supports:

1. `hf-zerogpu-trellis` — preferred hosted provider.
2. `hf-zerogpu-sf3d` — faster hosted fallback, selected only by the user.
3. `local-sf3d` — optional Apple Silicon fallback installed and run locally only after a separate license and disk-impact approval.

The monetary budget is immutable: `maxCostUsd = 0`. The feature must fail closed if a provider, account, endpoint, or request could create a charge.

## 2. Problem

The core pipeline reconstructs an object from images as procedural TypeScript and Three.js geometry. This preserves editability and avoids runtime assets, but a single image provides limited evidence for curved surfaces, hidden faces, irregular silhouettes, and dense architectural details.

The repository already contains `integrations/mesh3d/generate_reference_mesh.py`, which can call a hosted TRELLIS Space and emit GLB and OBJ files. It is a standalone external helper rather than a complete pipeline capability. It does not provide:

- a stable provider interface;
- a strict zero-spend admission gate;
- reviewable routing and fallback decisions;
- quota-aware caching and resumability;
- normalized provenance and failure reports;
- a Stable Fast 3D fallback;
- an explicit user approval boundary for uploads and gated licenses.

## 3. Goals

- Generate a GLB/OBJ reference proxy from one or more input images without monetary spend.
- Reuse the current TRELLIS integration rather than replacing it.
- Make provider selection explicit, inspectable, and deterministic.
- Preserve the procedural img2threejs output as the default deliverable.
- Treat generated meshes as fallible measurement evidence, never as ground truth.
- Avoid duplicate generation through content-addressed caching.
- Preserve completed provider output if later conversion, validation, or review fails.
- Keep credentials out of source files, command arguments, logs, reports, and Git.
- Work from macOS by using hosted ZeroGPU services; offer local SF3D only where the Mac meets its requirements.

## 4. Non-goals

- Paid Tripo, Meshy, Replicate, Fal, cloud GPU, or Hugging Face upgraded hardware.
- Automatic credit purchase, automatic refill, subscription activation, or billing setup.
- Circumventing provider quotas, queues, authentication, license gates, or rate limits.
- Automatically accepting Hugging Face or Stability AI terms.
- Training or fine-tuning a 3D model.
- Running TRELLIS or TRELLIS.2 locally on Apple Silicon.
- Shipping a downloaded GLB silently in place of the code-only Three.js deliverable.
- Treating a successful provider response as proof that the visual result is acceptable.
- Background retry loops that repeatedly consume daily free quota.

## 5. Considered approaches

### A. Keep the standalone TRELLIS script

Smallest change, but it leaves cost admission, provider fallback, caching, provenance, and resumability as manual conventions. This does not meet the zero-spend safety requirement.

### B. Provider-neutral free-assist orchestrator — selected

Wrap the existing TRELLIS logic and a new SF3D adapter behind a narrow provider protocol. A deterministic preflight admits only providers proven to be free for the current request. This adds the required controls without changing the stdlib-only `forge/` core.

### C. Import provider GLBs directly at runtime

Fastest route to visible detail, but it breaks the code-only runtime contract, reduces semantic editability, and makes the application depend on externally generated topology. This remains outside the first implementation.

## 6. Architecture

The integration remains isolated under `integrations/mesh3d/`. The stdlib-only `forge/` pipeline receives only normalized artifacts and reports.

```text
reference image(s)
        |
        v
free-assist preflight
  - provider allowlist
  - live endpoint class
  - authentication state
  - zero-cost proof
  - local capability check
        |
        +-- DENY / NEEDS_USER_ACTION --> report, no generation
        |
        v
explicit upload/run approval
        |
        v
one selected provider adapter
        |
        v
immutable raw result + generation receipt
        |
        v
GLB inspection and GLB -> OBJ normalization
        |
        v
img2threejs review gates
        |
        v
procedural TypeScript reconstruction
```

### 6.1 Provider protocol

Each adapter implements the following conceptual interface:

```python
class FreeMeshProvider(Protocol):
    provider_id: str

    def preflight(self, request: GenerationRequest) -> ProviderPreflight: ...
    def generate(self, request: GenerationRequest, run_dir: Path) -> RawGeneration: ...
    def normalize(self, raw: RawGeneration, run_dir: Path) -> MeshArtifacts: ...
```

Adapters must not decide whether another provider should run. Routing belongs exclusively to the orchestrator.

### 6.2 Provider registry

The registry contains only the three provider IDs in this specification. Provider URLs and expected free execution classes are exact allowlisted values, not arbitrary user-controlled endpoints.

An override such as `--space` may select another Hugging Face Space only in developer mode. Developer-mode overrides are always classified `UNVERIFIED` and cannot pass the zero-spend gate without a new reviewed registry entry.

### 6.3 Separation from `forge/`

Third-party dependencies such as `gradio_client`, `huggingface_hub`, `torch`, and SF3D remain in the integration environment. No dependency is added to the stdlib-only `forge/` core.

## 7. Zero-spend contract

Every run has the following immutable policy:

```json
{
  "maxCostUsd": 0,
  "allowPaidFallback": false,
  "allowCreditPurchase": false,
  "allowAutomaticRetry": false,
  "allowAutomaticProviderSwitch": false
}
```

There is no command-line flag that raises `maxCostUsd` in this feature. Paid support, if ever desired, requires a separate specification and implementation.

A hosted provider is admitted only when all of these are true:

- its provider ID and endpoint match the checked-in allowlist;
- its live Hugging Face hardware class is ZeroGPU or another explicitly reviewed free class;
- the request does not require upgraded hardware, prepaid credits, or a subscription;
- the client is not configured to extend quota using paid credits;
- the provider terms do not require an action that the user has not completed;
- the user has explicitly approved uploading the named source images to that provider;
- no prior successful result exists for the same cache key.

If live zero-cost status cannot be established, preflight returns `DENY`, not a warning.

## 8. Provider behavior

### 8.1 `hf-zerogpu-trellis`

- Uses the existing `trellis-community/TRELLIS` Space and current session bootstrap sequence.
- Accepts one primary image and optional additional views.
- Preserves deterministic seed and mesh simplification settings in the receipt.
- Requests one generation only.
- Emits the downloaded GLB as an immutable raw artifact before local conversion.
- Remains the preferred provider because the current pipeline already understands its response and multi-view input.

### 8.2 `hf-zerogpu-sf3d`

- Uses the official `stabilityai/stable-fast-3d` ZeroGPU Space.
- Is never invoked automatically after TRELLIS failure.
- Requires a new user approval because it uploads the images to a different provider surface.
- Normalizes its output into the same GLB/OBJ/report contract.
- Records the Stability AI model/license identifier in provenance.

### 8.3 `local-sf3d`

- Is disabled until a local capability preflight passes.
- Requires Apple Silicon or a supported CPU/GPU path, sufficient free disk, and sufficient memory.
- Requires the user to review and personally accept the gated model terms; the pipeline must not accept them.
- Requires a separate installation approval because it downloads model weights and creates an environment.
- Does not require or use billing credentials.
- Uses CPU fallback when MPS is unavailable or the configured memory safety threshold rejects MPS.

Local installation is a fallback capability, not part of the hosted-provider MVP acceptance test.

## 9. Routing and user approvals

Preflight returns exactly one of:

- `ALLOW` — the selected provider is proven zero-cost and all non-financial prerequisites are satisfied.
- `NEEDS_USER_ACTION` — login, email verification, license acceptance, upload approval, or local installation approval is missing.
- `DENY` — free status is unverified, quota is known exhausted, endpoint class is not allowlisted, or a paid action may occur.
- `UNAVAILABLE` — endpoint is paused, unhealthy, incompatible, or the local machine lacks required capability.

The orchestrator presents the decision and evidence before any provider call. Confidence or provider availability must never start a job automatically.

Fallback is manual and resumable:

1. A failed TRELLIS run records its receipt and stops.
2. The user may approve SF3D hosted as a new run.
3. The user may separately approve local SF3D installation and execution.

## 10. CLI contract

The intended interface is:

```bash
python -m integrations.mesh3d.free_assist preflight \
  reference/front.png \
  --provider hf-zerogpu-trellis \
  --out-dir artifacts/my-object/free-assist

python -m integrations.mesh3d.free_assist generate \
  reference/front.png \
  --provider hf-zerogpu-trellis \
  --out-dir artifacts/my-object/free-assist \
  --approve-upload

python -m integrations.mesh3d.free_assist resume \
  --run artifacts/my-object/free-assist/runs/<run-id>
```

`generate` refuses to run without a current passing preflight and explicit `--approve-upload`. The approval applies only to the listed files, provider ID, endpoint, parameters, and cache key.

Authentication is discovered through the supported Hugging Face login/token store or `HF_TOKEN`. Tokens are never accepted as CLI values.

## 11. Artifacts and provenance

Each run writes to a unique directory and never overwrites a previous run:

```text
free-assist/
  cache-index.json
  preflight.json
  runs/<run-id>/
    request.json
    provider-receipt.json
    status.json
    raw/reference.glb
    normalized/reference.glb
    normalized/reference.obj
    normalized/reference-mesh.json
    review/admission.json
    review/preview.png
```

`request.json` stores hashes and relative artifact paths, not credentials. `provider-receipt.json` records provider, endpoint, model/version when exposed, seed, parameters, timestamps, provider task ID, quota class, and declared monetary cost of zero.

Provider URLs that embed temporary signatures are redacted. Logs and reports must never contain authorization headers, query tokens, cookies, or full environment dumps.

## 12. Cache and quota preservation

The cache key is computed from:

- ordered SHA-256 hashes of all input images;
- provider ID and reviewed endpoint version;
- generation parameters;
- normalizer version.

A successful cached generation is reused without network access. A failed local normalization resumes from `raw/reference.glb`; it must never regenerate the mesh merely because conversion or validation failed.

Provider failures are not automatically retried. Retry requires a new explicit run approval so free daily quota cannot be consumed in a loop.

## 13. Generated-mesh admission

The generated mesh is a proxy, not ground truth. Before it can influence reconstruction, the pipeline checks:

- valid GLB magic and readable JSON chunk;
- non-empty mesh and material inventory;
- supported compression or a usable normalized OBJ fallback;
- finite bounds and non-degenerate scale;
- triangle and vertex budgets;
- axis agreement between GLB and OBJ;
- preview render against the original image;
- silhouette, aspect, and scale metrics;
- human review of invented hidden surfaces where relevant.

A provider task marked successful can still fail mesh admission. Failure preserves all evidence and leaves the procedural image-only path available.

## 14. Privacy, licensing, and credentials

- The preflight identifies exactly which files will leave the machine.
- No upload occurs during preflight or automated tests.
- Public/free provider outputs must be treated according to their current visibility and license terms.
- The system records, but does not interpret as legal advice, the provider/model license identifier.
- Hugging Face and Stability AI terms are presented for user action when required; the pipeline cannot click or accept them.
- `.env`, shell history, repository configuration, and generated reports must not store tokens.
- Tests use fake tokens and fixture endpoints only.

## 15. Failure handling

Normalized failure categories are:

- `free_status_unverified`
- `quota_exhausted`
- `queue_timeout`
- `provider_unavailable`
- `authentication_required`
- `license_acceptance_required`
- `upload_not_approved`
- `local_capability_missing`
- `invalid_provider_response`
- `invalid_glb`
- `normalization_failed`
- `mesh_admission_failed`

Every failure records whether the run is resumable and the last durable artifact. Raw successful provider output is always a recovery boundary.

## 16. Testing strategy

### Unit tests

- Provider registry admits only reviewed free providers.
- Any paid, unknown, or overridden endpoint fails closed.
- `maxCostUsd` is always zero and cannot be raised.
- Tokens and signed URLs are redacted from reports and exceptions.
- Cache keys change when inputs, provider, endpoint version, or parameters change.
- Existing raw GLB output prevents duplicate provider generation.
- Provider failure never triggers an automatic retry or provider switch.

### Integration tests

- Fixture TRELLIS and SF3D responses normalize to the same artifact contract.
- Interrupted normalization resumes from the saved raw GLB.
- Compressed and uncompressed GLBs follow the correct admission paths.
- A provider success with an invalid GLB is rejected.
- The procedural image-only pipeline remains usable after every failure category.

### Live acceptance

One user-approved TRELLIS ZeroGPU run is performed with a non-sensitive test image. Acceptance requires:

- a live preflight proving an allowlisted ZeroGPU endpoint;
- no payment method, subscription, purchased credit, or paid hardware action;
- exactly one provider generation request;
- persisted GLB, OBJ, provenance, preview, and admission report;
- confirmation that no automatic fallback ran;
- a browser comparison against the input image.

The live run is not part of automated CI because it consumes shared free quota and depends on an external queue.

## 17. Documentation requirements

Documentation must explain:

- what “free” guarantees and what it does not;
- current ZeroGPU quota and queue caveats without hard-coding them as permanent facts;
- how to log in through supported Hugging Face tooling;
- how to inspect a preflight before approving an upload;
- how to resume without regeneration;
- why Meshy Free is excluded from API automation;
- why Tripo Studio Free is a manual option rather than a guaranteed free API provider;
- why local TRELLIS is excluded on Apple Silicon;
- Stable Fast 3D license, model-access, memory, and disk prerequisites.

## 18. Acceptance criteria

The feature is complete only when all of the following are true:

- The provider-neutral orchestrator supports the three provider IDs in this specification.
- Preflight is read-only and performs no image upload.
- Unknown, paid, or unverifiable providers return `DENY`.
- No code path can create monetary spend or extend paid quota.
- Every network generation requires exact provider/file approval.
- No failure automatically consumes a second generation.
- Successful raw outputs are cached and resumable.
- TRELLIS and SF3D hosted outputs normalize to one artifact schema.
- Credentials are absent from Git, CLI arguments, reports, and captured logs.
- Generated meshes pass explicit admission before influencing reconstruction.
- The original procedural pipeline and existing standalone script remain backward compatible.
- Automated tests use no external quota.
- A separately approved live ZeroGPU run demonstrates the end-to-end path without monetary spend.

## 19. Explicit exclusions from the first implementation

- Tripo OpenAPI adapter.
- Meshy API adapter.
- Paid Hugging Face inference endpoints or upgraded Spaces.
- TRELLIS.2 local installation.
- Automatic model ranking based on visual scores.
- Direct runtime shipping of provider GLBs.
- Batch generation.
- Web UI.
- Provider webhooks.

