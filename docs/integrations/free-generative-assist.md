> Last updated: 2026-09-01 00:50

# Free generative assist

This optional integration obtains a generated GLB/OBJ reference proxy while enforcing an immutable `maxCostUsd = 0`. It does not replace the procedural Three.js deliverable, and a structurally valid provider result is not ground truth or visual acceptance.

The reviewed providers are:

- `hf-zerogpu-trellis` at `trellis-community/TRELLIS`, the preferred hosted multi-view route.
- `hf-zerogpu-sf3d` at `stabilityai/stable-fast-3d`, a manual single-image fallback.
- `local-sf3d`, an already-installed Stable Fast 3D checkout selected separately.

There is no paid provider, arbitrary endpoint, cost override, refill, automatic provider switch, or automatic retry. Hosted admission requires the live Hugging Face metadata to report the reviewed Space as `RUNNING`, its domain as `READY`, and both current and requested hardware as `zero-a10g`. Missing or changed evidence fails closed.

## Hosted workflow

Log in through supported Hugging Face tooling; credentials are discovered from that token store or `HF_TOKEN` and are never command arguments:

```bash
uv sync --project integrations/mesh3d
hf auth login

uv run --project integrations/mesh3d python -m integrations.mesh3d.free_assist preflight reference/front.png \
  --provider hf-zerogpu-trellis \
  --out-dir artifacts/my-object/free-assist
```

Read `preflight.json`: it names the exact files, provider, endpoint evidence, parameters, cache key, and zero-cost policy. Preflight performs no upload. Only after reviewing it, run one request:

```bash
uv run --project integrations/mesh3d python -m integrations.mesh3d.free_assist generate reference/front.png \
  --provider hf-zerogpu-trellis \
  --out-dir artifacts/my-object/free-assist \
  --approve-upload
```

`--approve-upload` applies to that invocation's provider, files, hashes, endpoint revision, parameters, and cache key. A failure stops; there is no automatic retry and Stable Fast 3D is never selected automatically. Switching hosted provider requires a new command and new upload approval.

ZeroGPU quotas, queue times, authentication rules, and Space availability are external live state. The tool therefore does not hard-code a permanent free-minute allowance: it re-checks hardware state before a non-cached run and denies uncertainty. It never buys credits or upgrades hardware.

## Cache and resume

The cache is keyed by ordered input hashes, provider, endpoint revision, parameters, and normalizer version. A verified completed match is reused before any metadata network request, upload, or generation. Raw output is written atomically to `runs/<id>/raw/reference.glb` and hashed in the provider receipt before local conversion begins.

If OBJ conversion or admission fails, resume from that durable GLB:

```bash
uv run --project integrations/mesh3d python -m integrations.mesh3d.free_assist resume \
  --run artifacts/my-object/free-assist/runs/<run-id>
```

Resume has no provider adapter and cannot consume ZeroGPU quota. It verifies the raw hash before deriving normalized artifacts.

## Local Stable Fast 3D

Local mode does not install software or accept model terms. The user must review and accept the gated Stability AI model license themselves, install the official checkout separately, then expose only these non-secret signals:

```bash
export SF3D_ROOT=/absolute/path/to/stable-fast-3d
export SF3D_MODEL_ACCESS_APPROVED=1

uv run --project integrations/mesh3d python -m integrations.mesh3d.free_assist generate reference/front.png \
  --provider local-sf3d \
  --out-dir artifacts/my-object/free-assist \
  --approve-local-run
```

Preflight requires `SF3D_ROOT/run.py`, at least 12 GiB free disk and 16 GiB physical/unified memory. Apple Silicon with at least 32 GiB is reported as eligible for experimental MPS; smaller supported machines are routed to CPU for safety. `--force-cpu` explicitly requests CPU. Installation, model downloads, and license acceptance require their own user-controlled process and are not performed here.

## Admission and privacy

Normalization produces GLB, OBJ, mesh inventory, provider receipt, status, and structural admission. The gate checks GLB v2 structure, mesh/material/BIN inventory, finite non-degenerate bounds, compression declarations, GLB/OBJ axis-scale agreement, and vertex/triangle ceilings. It deliberately leaves `admittedForProceduralInfluence: false` until a preview, silhouette/aspect/scale comparison, and human review of invented hidden surfaces are recorded through the normal img2threejs visual gates.

Reports redact authorization, cookies, HF tokens, bearer values, and signed URL query fields. Request artifacts store image names and hashes rather than credentials. Do not place `.env` files or tokens in the repository.

Tripo Studio Free remains a manual external option rather than a guaranteed free API provider. Meshy Free is excluded because its free plan does not provide the API automation/export contract required here. Local TRELLIS is excluded on Apple Silicon; TRELLIS' supported implementation is CUDA/Linux-oriented. These exclusions prevent a marketing-tier label from being treated as proof that an automated request is free.

## Verification boundary

Automated tests use fixture GLBs and injected metadata only; they make no provider calls. One separate live acceptance run against TRELLIS ZeroGPU remains intentionally unexecuted until the user explicitly approves a non-sensitive input upload and the consumption of one free-quota generation. That live check must retain the browser preview and comparison evidence.
