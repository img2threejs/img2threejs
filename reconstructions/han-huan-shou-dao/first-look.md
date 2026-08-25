# First look — 汉代环首刀三视图

Suitability verdict: **pass for a stylized procedural reconstruction**. The orthographic plate
provides face, side, and top evidence for the blade, disk guard, wrapped grip, fittings, and ring.
It supports strong real-time identity, but not artifact-grade material recovery or hidden joinery.

Authoritative reference: `references/chinese-swords/汉代环首刀三视图.jpg`.
The rusted floor relic is not a structural or scoring reference for this build.

The current model reads as the same Han huan-shou dao:

- smooth single-edged blade with distal thickness taper and a rising tip
- thin disk guard in YZ, circular from the thickness axis
- dark crossed cord wrap with six gilt lozenges
- front/rear gilt ferrules and a short ring neck
- shallow plate-shaped gilt ring with a real oval aperture
- procedural hamon lines and shallow ring engraving

Fresh visual gates:

- `diagnose_render.py`: PASS, silhouette IoU `0.8905`, aspect delta `0`, scale delta `0`
- `divine_eye.py`: PASS, fidelity `0.8583` against target `0.85`
- multi-angle: no degenerate view; profile area ratio `0.5408`
- map-stripped evidence is a dedicated unlit capture, not a copy of hero

The blockout review is accepted and the spec now unlocks `structural-pass`. Remaining visible
approximation is local: the three hamon lines are cleaner than the illustrated wave, ring ornament
is less dense, and the finish is stylized. Macro proportions and camera framing should stay fixed.

Preview:
`http://127.0.0.1:4173/reconstructions/han-huan-shou-dao/preview/index.html`

Full architecture, commands, evidence, and next-pass limits are in `HANDOFF.md`.
