"""Base-owned decision-only routing, before checklist initialisation.

Host API: instantiate ``route.Router`` with explicit trusted fallback/consent
callbacks and either a direct provider callback or ``provider_factory``.
``provider_factory.provider(original_context)`` returns a callback for exactly
that original trusted context; the controller invokes it with a path-stripped
projection. A CLI bridge must prove that projection differs ONLY by removal of
binding paths, and validate the returned projected digest. No plugin Python
code is imported across repositories; no credential/consent grant is created.

``Router.route`` takes a workspace-relative admitted PNG or baseline JPEG and
``img2.object-proposals`` v1:
{objects:[{objectId,label,role,approxBox,observations?,instanceOf?}]}.
IDs/labels use lowercase hyphenated opaque identifiers; boxes are normalised
[left,top,right,bottom] in UPRIGHT reference space after EXIF orientation.
Original source SHA, lossless upright reference SHA and pixel-centre transform
are retained. Explicit 2D ignores proposals and uses the whole upright image.
The result contains decision, decisionRef and sha256; executionAuthorized is
always false. All reference/crop/card/proposal/context records are content-bound.

CLI: ``python3 forge/stage0_route/route.py --workspace WORK --run ID --image
reference.png --objects objects.json``. --harness/--auth-cli name trusted local
executables, not shell strings. The standalone CLI performs real read-only
probes but has no model/paid/consent adapter enrollment. It requests input (exit
3), never manufactures an answer, and names img2-jev login for proven auth gaps
when a trusted consent adapter is available through the Python API.

D9: independently validated placements, real crops with 10% margin, visibility
rejection, same-label overlap merging (never collapsing explicit instances),
maximum 12 distinct route groups. All instance placements retain their own id,
validation, Box, crop and eligibility; routing runs once on their intersection.
``Router(..., second_proposals=callback)`` may invoke the trusted host callback
once when first-round missingObjects >= .7. It receives an
img2.decomposition-proposal-request v1 with the exact initial proposal set,
source/input/first-round digests and authenticated trigger decision IDs, and
returns img2.object-proposals v1 ADDITIONS with new object IDs. Exact request and
response snapshots are durable before admission; completed responses replay
without calling the host again, interrupted unresolved callbacks request input.
There is no third round; remaining missing objects request input. This host
adapter must independently enforce consent for any paid/licensed host action.
Low-IoU boxes trigger controller-backed Choice over the actual model/Jev crops.
Unresolved Choice retains Jev's box flagged low-confidence and blocks promotion;
valid uncertainty never activates model override. All calls share controller
object deadlines, run budget and fresh consent checks.

Baseline JPEG normalization is stdlib-only and supports all eight EXIF
orientations. Progressive/unsupported JPEG, malformed/ambiguous orientation,
palette/interlaced PNG and EXIF-bearing PNG fail explicitly rather than guessing.
Routing sends trusted routeId plus opaque candidate id, never display-derived
identity, and explicitly references its authenticated object-crop image. Actual
image disclosure still requires the controller's four-purpose consent adapter.

Parent verification: call verification_snapshot OUTSIDE the controller lock;
inside ``controller.workspace_transaction(consent=trusted_callback) as verify``
call ``router.verify_decision(ref, verify=verify, discovered=snapshot)``. This
checks controller-journal seals for EVERY exact proposal round, initial inputs,
second-round trigger records, and program-owned round-to-consultation links.
It revalidates every proposed object, including dropped/merged objects and each
placement instance, against live evidence/consent and deterministic eligibility,
crop pixels and arbitration. It makes no provider call. Context/cache digests do
not include aggregate revision; the controller independently rebinds unchanged
cached records to a new sealed round after live verification.
``proposalRounds`` records round, proposal binding, parentRoundSha256,
triggerDecisionIds (empty for round 1; round-1 trigger receipts for round 2),
missingObjectDecisionIds (that round's observed missing-object receipts), and
the second proposal request binding when applicable. Sealing occurs before each
round's first consultation; object revisions remain independent. Consent changes
change the initial input digest and aggregate revision before any new seal.
Best-of-N and low-confidence crops remain unresolved for execution. A sealed
proposal proves which objects were proposed, not that the image has no omitted
objects. executionAuthorized remains false pending parent integration and
qualification. No state, child run or composition hook is implemented here.
"""
