# End-to-end routing acceptance scripts

Two scripts in `scripts/e2e/` set up a clean, isolated harness home and route
one reference image through `forge/route.py`. Routing is decision-only. It
chooses an owner route and builds nothing, so these scripts do not prove
construction, terminal acceptance or delivery.

Neither script reads or writes the real `~/.img2` or the real `HOME`, and
neither authorizes Jev consent.

## 1. Provision an isolated home

```bash
python3 scripts/e2e/provision_home.py \
  --home /tmp/img2-e2e/iso \
  --harness-src /path/to/harness --harness-ref <harness-sha> \
  --node /path/to/node \
  --plugin character=/path/to/character@<sha> \
  --plugin cs2=/path/to/cs2@<sha> \
  --plugin interior=/path/to/interior@<sha> \
  --plugin jev=/path/to/jev@<sha> \
  --plugin pixi2d=/path/to/pixi2d@<sha>
```

- `--home` must not exist or must be an empty directory. The script refuses
  anything that overlaps the real home directory or `~/.img2`.
- All five plugins must be given. SHAs are full 40- or 64-character commits.
- Every command runs as an argv array, with no shell and a timeout, under a
  sanitized environment (`HOME`, `PATH`, `TMPDIR`, `XDG_*`, `GIT_*`,
  `BLENDER_USER_*` all point inside `--home`). Steps: `git clone --no-hardlinks`,
  `git checkout --detach`, `img2 install --from`, `img2 add ... --ref ... --plugin
  ... --allow-any-source --yes` per plugin, `img2 sync`, `img2 sync --check`,
  `git rev-parse HEAD` of the installed harness, `img2 doctor --json`.
- It writes `--home/provision.json`: every command with exit code, stdout and
  stderr; the doctor result; the pins (harness sha, `bin/img2.mjs` sha256, node
  sha256, per-plugin resolved sha). `status` is `ok` only if every step passed,
  the installed harness HEAD and every registered plugin sha equal what was
  requested, and doctor reported zero failures.
- Exit codes: 0 ok, 1 a step failed or doctor is red, 2 refused before running.
- Doctor warnings are printed and recorded, not hidden. The reviewed pins
  produce two `WARN` findings (jev and character declare several capabilities).
  Those warnings are not failures.

The isolated Python/keychain setup is not replicated. The scripts run `python3`
from the invoking environment (`sys.executable`). Jev credentials come from the
user's keychain or `TYPESAFE_API_KEY`, as for any Jev run.

### Required `provision.json` contract

`route_one.py --home ROOT` requires **`ROOT/provision.json`**, for example
`/tmp/img2-e2e/iso/provision.json` above. `ROOT` is the E2E root, not `HOME`
(`ROOT/home`) or `IMG2_HOME` (`ROOT/home/.img2`). This record is produced by
`scripts/e2e/provision_home.py`, **not by harness `setup`**. The harness's
`IMG2_HOME/runtime/provision.json` is a different runtime receipt and cannot
be used in its place.

Prefer the provisioning script with a fresh/empty `--home`. If an installation
was already prepared with harness `setup`, the E2E record must be supplied
separately from verified installed paths and pins. Do not mark a failed or
unchecked installation `ok`. The JSON object fields consumed by `route_one.py`
are:

| Field | Type and meaning |
|---|---|
| `status` | String, exactly `"ok"`; any other value is refused. |
| `img2Home` | String, absolute isolated harness home (sets `IMG2_HOME`). |
| `pins` | Object containing the fields below. |
| `pins.harnessRoot` | String, absolute installed harness checkout (sets `IMG2_HARNESS_DIR`). |
| `pins.harnessSha` | String, full installed harness commit, passed as `--harness-sha`. |
| `pins.harnessExecutable` | String, absolute installed `bin/img2.mjs` path. |
| `pins.harnessExecutableSha256` | String, 64 lowercase hex SHA-256 characters of that executable. |
| `pins.node` | String, absolute Node executable path; its parent is prepended to `PATH`. |
| `pins.plugins` | Object containing `jev`. |
| `pins.plugins.jev` | Object containing `installedDir`. |
| `pins.plugins.jev.installedDir` | String, absolute installed Jev directory containing `tools/jev.py`. |

The full producer record also includes `kind: "img2.e2e-provision"`, `version: 1`,
command/doctor evidence, environment and additional pins (including node and
registry hashes and all five plugins). The route runner does not consume those
extra fields; this table describes its input, not a substitute for provisioning
verification. Missing/malformed records refuse with exit 2 before any subprocess
or workspace creation. `--dry-run` still requires this record.

## 2. Route one image

```bash
python3 scripts/e2e/route_one.py \
  --home /tmp/img2-e2e/iso --workspace /tmp/img2-e2e/ws-cube \
  --image /path/to/reference.png --label red-cube --kind 3d \
  --run-id cube-1 [--intent rig] [--box x0,y0,x1,y1] [--token-budget 256000] [--dry-run]
```

It creates the workspace, copies the image to `ref.png` (or `ref.jpg`), and for
`--kind 3d` writes `objects.json` with one object covering the whole image
(or `--box`). `--role` is `subject` (default), `prop`, `scene-container` or
`set-dressing`. Repeat `--observation key=value` to supply observations: values
are parsed as JSON when possible, otherwise kept as strings; a repeated key uses
the last value. For example, `roomCount=1` is numeric and
`enclosure=enclosed-room` is a string. A 2D run writes no `objects.json`, matching
`forge/route.py`. The workspace must be new, or a directory this script already
prepared for identical input, including role and observations. `--label` is
lowercase letters, digits and hyphens. Image must be PNG or baseline JPEG.

For a single enclosed room:

```bash
python3 scripts/e2e/route_one.py \
  --home /tmp/img2-e2e/iso --workspace /tmp/img2-e2e/ws-room \
  --image /path/to/room.png --label room --kind 3d --run-id room-1 \
  --role scene-container \
  --observation enclosure=enclosed-room --observation roomCount=1
```

Unlike provisioning, the route runner inherits the caller's environment, including
`IMG2_*` runtime selector variables and `PATH`; it overrides `IMG2_HOME` and
`IMG2_HARNESS_DIR` with the provisioned pins and prepends the provisioned Node
directory to `PATH`. Interior requires `interior-bpy>=4.3`, probed through the trusted
`IMG2_BLENDER_PYTHON` selector (process environment, then validated provisioned selectors).
Its venv invocation path is retained; an unrelated `blender` CLI cannot substitute.
Missing bpy makes interior ineligible without breaking unrelated child creation.
Missing allowlisted legacy dependency executables remain `dependency-missing:<probe>`;
actual gate failures and harness pin failures remain fail-closed.

It then runs `jev.py consent decision-status` for the workspace and run id.

### What the human must approve

If the four decision purposes are not authorized, the script prints one command
and exits **20** without running `forge/route.py`:

```
IMG2_HOME=... IMG2_HARNESS_DIR=... python3 .../jev/tools/jev.py consent authorize-decisions \
  --workspace WS --run-id RUN --token-budget 256000 \
  --accept-image-disclosure --accept-paid-calls
```

Running it is a human decision with two consequences:

- Jev receives the reference image and derived crops (`--accept-image-disclosure`).
- Routing, build-strategy, quality-review and repair calls may be charged, up to
  the shared token budget (`--accept-paid-calls`). Each dispatch reserves
  64,000 tokens, so the script refuses budgets below that.

If Jev credentials are missing or rejected, the route output carries
`requestedCommand: img2-jev login`. Run it yourself. Never paste the key into a
chat or a script.

After approving, re-run the same `route_one.py` command. A grant covers one
workspace and run id; a different run id needs a new grant. The script prints a
`revoke-decisions` command at the end. Revoking is optional and also manual.

### Reading the output

```
status: decided
owner route [whole-image]: pixi2d:<sha256>
decided by: Jev (jev-1.14.0)
journal: WS/.img2threejs/route/journal.jsonl
decision: .img2threejs/route/decision-<sha256>.json
decision record: .img2threejs/decisions/routing/<id>.json group=routing decider=jev:... tokens in/out=2816/166
tokens charged: 2982
```

- `status`: `decided` is the only success value. `request-input` means the
  route stopped; `reason:` gives the code (for example `credential-missing`,
  `consent-required`, `budget-exhausted`, `explicit-2d-unavailable`).
- `owner route`: the owner chosen per object, read from the content-addressed
  object inventory. `decided` can still list objects as `unresolved`
  (for example an unresolved best-of-N); the script then exits 21.
- `decided by`: `Jev (<model>)` when Jev answered. `FALLBACK MODEL <id>` means a
  host-approved fallback program answered after a journalled Jev outage. This
  script passes no `--fallback-command`, so a fallback model appearing here
  would indicate a configuration you did not request. It is never Jev acceptance.
- `journal` and `decision`: hash-chained route journal and the immutable
  decision file. `decision record` lines are the per-consultation records.
- `tokens charged`: sum of `chargedTokens` in Jev's private budget ledger for
  this run. It includes the full reservation for failed or unknown attempts,
  so it can exceed the usage shown on the record lines. `unknown` means the
  ledger could not be read.
- `unresolved:` lines list suspension reasons and objects that did not resolve.

Exit codes: 0 decided and resolved, 1 route crashed or printed no result,
2 refused before running, 20 consent not authorized, 21 finished without a
resolved decision.

`--dry-run` prints the `decision-status`, consent and route argv arrays,
executes nothing and creates no workspace.

## Tests

```bash
python3 -m unittest forge.tests.test_e2e_scripts
```

They use a stubbed subprocess runner and need no network, Jev, model or install.

The core release-lock parity test uses `IMG2_HARNESS_DIR/plugins.lock.json`
(or a sibling `harness/plugins.lock.json` when the variable is unset). It skips
cleanly when that file is absent; when available, mismatched Jev commits fail:

```bash
IMG2_HARNESS_DIR=/path/to/harness python3 -m unittest \
  forge.tests.test_regen_jev_release_lock.HarnessPinParityTests
```

The decision fixture in the tests is a trimmed copy of the real artifact layout
from a recorded routing run, not live output.
