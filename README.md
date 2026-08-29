<div align="center">

<img src="assets/logo.svg" width="112" height="104" alt="img2threejs logo" />

# img2threejs

**Rebuild the object in a reference image as a code-only, procedural Three.js model.**

Quality-gated, animation-ready, and deliberately token-efficient — reconstruction-by-code, not photogrammetry, mesh extraction, or downloaded art packs.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-1.5.1-green.svg)](CHANGELOG.md)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)
[![Runtime](https://img.shields.io/badge/runtime-Three.js-000000.svg)](https://threejs.org)
[![Tooling](https://img.shields.io/badge/tooling-Python%203.10%2B%20stdlib-3776ab.svg)](forge)
[![Sponsor](https://img.shields.io/badge/Sponsor-Ko--fi-FF5E5B.svg?logo=kofi&logoColor=white)](https://ko-fi.com/iamnick)
[![Scripts](https://img.shields.io/badge/scripts-forge%20%2F%20scripts-3776ab.svg)](scripts)
[![Sponsored by Atlas Cloud](https://img.shields.io/badge/Sponsored%20by-Atlas%20Cloud-000000.svg?labelColor=1a1a1a&logo=data:image/svg%2Bxml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCA4NDUuOTUgNzkyIj48cGF0aCBmaWxsPSIjZmZmIiBkPSJNNzEyLjA1LDU5NC4zTDQyMi45OCwxNS40NiwxMzMuOSw1OTQuM2wtOTEuMDEsMTgyLjI1YzU3LjE4LTM0LjU1LDExOS40OS02MS40LDE4NS40NC03OS40NSw2Mi4wMi0xNi45NywxMjcuMjQtMjYuMjEsMTk0LjY1LTI2LjIxLDM0LjY5LDAsNjguNzksMi40OSwxMDIuMTksNy4xOGwtNjUuNzItMTQxLjc2Yy0xOC42MS0zLjIzLTEwMS4yOC0zLjIzLTE2MS44Myw5Ljg0bDEyNS4zNi0yNzMuMTYsMTk0LjY1LDQyNC4xMmMuMzUuMS42OS4yMSwxLjAzLjMsNjUuNTcsMTguMDQsMTI3LjUzLDQ0Ljc4LDE4NC40MSw3OS4xNGwtOTEuMDEtMTgyLjI1WiIvPjwvc3ZnPg%3D%3D)](https://www.atlascloud.ai/console/coding-plan)
[![Sponsored by Tripo](https://img.shields.io/badge/Sponsored%20by-Tripo-000000.svg?labelColor=1a1a1a&logo=data:image/svg%2Bxml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbC1ydWxlPSJldmVub2RkIj48cGF0aCBmaWxsPSIjZmFjZTAwIiBkPSJNMTIuNjYxIDQuNzUyYS4wNC4wNCAwIDAwLS4wMTMtLjA1NS4wMzguMDM4IDAgMDAtLjAyLS4wMDVINi40OTZhLjE5LjE5IDAgMDEtLjE2NS0uMDkyTDQuMjQ1IDEuMDUyYS4wMzQuMDM0IDAgMDEuMDE0LS4wNDdBLjAzOC4wMzggMCAwMTQuMjc1IDFjNS40OTIgMCAxMC43MzMgMCAxNS43MjEuMDAyIDIuMDg5IDAgMy42OTkgMS41NCA0LjAwMyAzLjU5OC4wMDguMDYyLS4wMTkuMDkyLS4wOC4wOTJoLTYuNjdhLjIwNC4yMDQgMCAwMC0uMTc0LjFjLTEuNDE0IDIuNDEtMi44MiA0LjgwMy00LjIxOCA3LjE3OC0uMjguNDcyLS45Mi42Mi0xLjM3My4zNDItLjE5LS4xMTYtLjM1Ny0uMzA0LS41MDItLjU2MWEzNy45MTcgMzcuOTE3IDAgMDAtLjg4Mi0xLjQ4OWMtLjIyMi0uMzU2LS4yMzQtLjcwNy0uMDM1LTEuMDUyYTY2MS43NyA2NjEuNzcgMCAwMTIuNTk3LTQuNDZ2LjAwMnoiLz48cGF0aCBmaWxsPSIjZmZmIiBkPSJNMTAuNzcyIDE2Ljk4NmMuNTcuOTcyIDEuOTM1LjkxNiAyLjQ4OS0uMDI4TDE5IDcuMTY0YS4xMjcuMTI3IDAgMDEuMTE2LS4wNjdoNC4yM2MuMDE3IDAgLjAzLjAxNC4wMjguMDMgMCAuMDA1LS4wMDEuMDEtLjAwMy4wMTMtMi42MDUgNC40MzItNS4yMzIgOC45MDYtNy44OCAxMy40Mi0xLjI4MyAyLjE5MS00LjI3OCAyLjUxNy02LjE3OS45NDctLjMwOC0uMjU0LS42NjUtLjcyNy0xLjA2OS0xLjQxNy0yLjUyNS00LjMyNC01LjA5OC04LjcxLTcuNzItMTMuMTYyLTEuMDYzLTEuODAzLS40MjQtMy45NDcgMS4xOS01LjIuMDUzLS4wNDEuMDk1LS4wMzMuMTI5LjAyMyAyLjkwNSA0Ljk1IDUuODgyIDEwLjAyOSA4LjkzIDE1LjIzNnoiLz48L3N2Zz4%3D)](https://www.tripo3d.ai/)
[![Sponsored by Hyper3D](https://img.shields.io/badge/Sponsored%20by-Hyper3D-000000.svg?labelColor=1a1a1a&logo=data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAAAXNSR0IArs4c6QAAAERlWElmTU0AKgAAAAgAAYdpAAQAAAABAAAAGgAAAAAAA6ABAAMAAAABAAEAAKACAAQAAAABAAAAIKADAAQAAAABAAAAIAAAAACshmLzAAADlElEQVRYCeWWbWiOURjH9wzDZoYs5i3aF%2FEBiTYZRQ35qGxS%2B2AoFNoHRcl7vlETGcWX1aLkLbKsJTNiXmpKpExt5v2lGWNte%2Fz%2Bd%2Ff9dO6z57mfe08p5arfc851netc57rP23PS0v53iYSZgGg0Ogy%2F5VACU%2BAnnIlEIjWUf1cYvAgaIZ7sxxjqI1LKkuCb4Ue8kV1bB%2BXElIIn60TgLdDnDhRULEsWa8DtjKZp7woa1W37TTljwAMEdSBgFjS5AyQrzuOQ7jKCcnBQ7FBtBCmHZPIdh0oYpaCUObATnoCS3wPzwwzo28F0GkKnezDX7fyJ8gF8hT54Bc3wmCP4mtIn9C%2FCcBbyoQv2QSW%2BqicXAhRAL7yFU3AI6uEoTE8ewZmNqfg%2BB8kL0FFdEqavpnI9qPNe0HRKdkOGHQBbBIbbdunYi6EbfsER%2BAC6yBILDgqoo3cavBOwPXEPZyDNWNxjiP0iSA6CTks7zAyKp8xLwBv8ZKCz24j%2FMVhj%2B2JbCRItoy4syW0YavvGdBovywvRHpgcawio4LcBWmG06YaeB7pFlUAneFJm%2BqWbCvWXrn6FndtqtSVSJ9GgPTLecviG3gDZYH71VjLRn5sjdgKNrv2CWwYWBNItOBZqYZ7l3IN%2BHyaAeUHNQS8AR8wGGe6AvlznP64w6FIaRrosoNTAs6AOTNGXt8Em00hdH10It8CXWRrT%2FpEBTmA3p0x%2BppSj2JtOF403e56vlmA25HoGo1TijtgzIONxiK2R4%2BX%2F6fWrjnaO3xbLnoduJ%2Bq55HgVew%2FIHoUMZiLTc7JKe3Y09TuYPV3VpixE8Z0Ms9Grx2aAAZWMprcCpsEzbFUErqJuyk0Ubbz3oMFr8NETzZagS6fddtYltBbiyeF%2BziEMBCqNF8y1lfpCYNQ1fDegw0batPNDCb6ZkAstYMsjDP6lwaA1b7Y9DV033S5YB1mJsqAtGyrAeSVRFkIj6CbUdVwH%2BWb%2F2HuAhus0rDAbrXotus61LhKt%2FTX4DBLNjv7tVkE9bGNfaDNrabW3dBx74Klnp%2B4XHBeD%2FrWCpJpGvRNMP%2FPh%2BoC20EvlzwCNzmVg%2FnGg9pOHWA7AJdDSeHKVyoCf6LEl8LIhiN5yekoVQ7x7Qq468w2gP68meAc3mN5uygFJvwS83iSyiPpqUELjYAwooU7QgG%2BgGjTwF8qUJGECZjSS0bWqy2cQdEBbKl9Lv39P%2FgAQ2ul5m76dAQAAAABJRU5ErkJggg%3D%3D)](https://hyper3d.ai/)

<table align="center">
  <tr>
    <td align="center"></td>
    <td align="center"><sub><b>DAILY</b></sub></td>
    <td align="center"><sub><b>WEEKLY</b></sub></td>
  </tr>
  <tr>
    <td align="right"><sub><b>Python</b></sub></td>
    <td><a href="https://trendshift.io/repositories/83608?utm_source=trendshift-badge&amp;utm_medium=badge&amp;utm_campaign=badge-trendshift-83608" target="_blank" rel="noopener noreferrer"><img src="https://trendshift.io/api/badge/trendshift/repositories/83608/daily?language=Python" alt="hoainho%2Fimg2threejs | Trendshift" width="250" height="55"/></a></td>
    <td><a href="https://trendshift.io/repositories/83608?utm_source=trendshift-badge&amp;utm_medium=badge&amp;utm_campaign=badge-trendshift-83608" target="_blank" rel="noopener noreferrer"><img src="https://trendshift.io/api/badge/trendshift/repositories/83608/weekly?language=Python" alt="img2threejs%2Fimg2threejs | Trendshift" width="250" height="55"/></a></td>
  </tr>
  <tr>
    <td align="right"><sub><b>All languages</b></sub></td>
    <td><a href="https://trendshift.io/repositories/83608?utm_source=trendshift-badge&amp;utm_medium=badge&amp;utm_campaign=badge-trendshift-83608" target="_blank" rel="noopener noreferrer"><img src="https://trendshift.io/api/badge/trendshift/repositories/83608/daily" alt="img2threejs%2Fimg2threejs | Trendshift" width="250" height="55"/></a></td>
    <td><a href="https://trendshift.io/repositories/83608?utm_source=trendshift-badge&amp;utm_medium=badge&amp;utm_campaign=badge-trendshift-83608" target="_blank" rel="noopener noreferrer"><img src="https://trendshift.io/api/badge/trendshift/repositories/83608/weekly" alt="img2threejs%2Fimg2threejs | Trendshift" width="250" height="55"/></a></td>
  </tr>
</table>

</div>

*Reference images reconstructed in code as animation-ready Three.js models, running live in the browser.*

### [→ Open the Live Demo Gallery](https://img2threejs.io/)

Every model in the gallery is generated code, running in your browser. No mesh files, no downloads.

---

## Live demos

Reconstructions built entirely from primitives, procedural shaders, and generated geometry. Open any model to orbit it, inspect its reference, and read the generated source.

| Demo | Subject | Built with | View | Source |
| --- | --- | --- | --- | --- |
| Dual-Sword Warrior — TypeScript procedural surfaces ⚠︎ | character | `v1.5.1` | [Live](https://img2threejs.io/#/demo/girl-character) | [code](https://github.com/img2threejs/img2threejs-showcase/blob/main/src/demos/girl-character/createGirlCharacterModel.ts) |
| Low-Poly Humanoid — Rigged Character ⚠︎ | character | `v1.5.0` | [Live](https://img2threejs.io/#/demo/low-poly-humanoid) | [code](https://github.com/img2threejs/img2threejs-showcase/blob/main/src/demos/low-poly-humanoid/createLowPolyHumanoidModel.ts) |
| ★ Talon Knife \| Doppler Ruby (Factory New) | object | `v1.4.4` | [Live](https://img2threejs.io/#/demo/talon-doppler-ruby) | [code](https://github.com/img2threejs/img2threejs-showcase/blob/main/src/demos/talon-doppler-ruby/createTalonDopplerRubyModel.ts) |
| AWP \| Medusa (Minimal Wear) · V2 rebuild | object | `V2` | [Live](https://img2threejs.io/#/demo/awp-medusa-v2) | [code](https://github.com/img2threejs/img2threejs-showcase/blob/main/src/demos/awp-medusa-v2/createAwpMedusaModelV2.ts) |
| Pikachu 10K Star Celebration ⚠︎ | character | `v1.5-beta` | [Live](https://img2threejs.io/#/demo/electric-mouse-mascot) | [code](https://github.com/img2threejs/img2threejs-showcase/blob/main/src/demos/electric-mouse-mascot/createElectricMouseMascotModel.ts) |
| Glock-18 \| Ghost Protocol (Well-Worn) | object | `v1.4.1` | [Live](https://img2threejs.io/#/demo/glock-ghost-protocol) | [code](https://github.com/img2threejs/img2threejs-showcase/blob/main/src/demos/glock-ghost-protocol/createGlockGhostProtocolModel.ts) |
| Classic Knife \| Fade (Minimal Wear) | object | `v1.3` | [Live](https://img2threejs.io/#/demo/classic-fade) | [code](https://github.com/img2threejs/img2threejs-showcase/blob/main/src/demos/classic-fade/createClassicFadeModel.ts) |
| BMX Endurance Bike | object | `v1.3` | [Live](https://img2threejs.io/#/demo/bmx-endurance) | [code](https://github.com/img2threejs/img2threejs-showcase/blob/main/src/demos/bmx-endurance/createBmxEnduranceBikeModel.ts) |
| M9 Bayonet \| Doppler Phase 2 | object | `v1.3` | [Live](https://img2threejs.io/#/demo/m9-doppler) | [code](https://github.com/img2threejs/img2threejs-showcase/blob/main/src/demos/m9-doppler/createM9DopplerModel.ts) |
| Sony WF-1000XM3 Earbuds + Case | object | `v1.2` | [Live](https://img2threejs.io/#/demo/sony-wf1000xm3) | [code](https://github.com/img2threejs/img2threejs-showcase/blob/main/src/demos/sony-wf1000xm3/createSonyWf1000xm3Model.ts) |
| ISSACA 12 Gauge Shotgun | object | `v1.2` | [Live](https://img2threejs.io/#/demo/issaca-shotgun) | [code](https://github.com/img2threejs/img2threejs-showcase/blob/main/src/demos/issaca-shotgun/createIssacaShotgunModel.ts) |
| Gerber Paracord Knife | object | `v1.2` | [Live](https://img2threejs.io/#/demo/gerber-knife) | [code](https://github.com/img2threejs/img2threejs-showcase/blob/main/src/demos/gerber-knife/createGerberKnifeModel.ts) |
| Doraemon House (isometric diorama) | object | `v1.2` | [Live](https://img2threejs.io/#/demo/doraemon-house) | [code](https://github.com/img2threejs/img2threejs-showcase/blob/main/src/demos/doraemon-house/createDoraemonHouseModel.ts) |
| War-Hauler "SECTOR 07" | object | `v1.2` | [Live](https://img2threejs.io/#/demo/warhauler) | [code](https://github.com/img2threejs/img2threejs-showcase/blob/main/src/demos/warhauler/createWarHaulerModel.ts) |
| Crowned Loot Chest ⚠︎ | object | `v1.2` | [Live](https://img2threejs.io/#/demo/crown-chest) | [code](https://github.com/img2threejs/img2threejs-showcase/blob/main/src/demos/crown-chest/createCrownChestModel.ts) |

⚠︎ marks a demo whose registry `status` is still `placeholder` rather than `final` — it renders, but it
is not finished work. The **Built with** column is the version each demo's own registry entry records in
`generatedWith`, not an inference from dates; `awp-medusa-v2` records `V2`, which is that demo's rebuild
pass rather than a release number. Rows are ordered newest first by the commit that added the demo.

The gallery source lives in [img2threejs/img2threejs-showcase](https://github.com/img2threejs/img2threejs-showcase). If this project is useful, a star on this repo helps others find it.

---

## What it does

You give it one reference image of an object. It produces a `THREE.Group` factory written in TypeScript that recreates that object from primitives, procedural shaders, and generated geometry — with a runtime hierarchy (pivots, sockets, colliders) so the result is ready to animate, not an inert lump.

It runs under Claude Code, Codex, or OpenCode. It is agent-agnostic: wherever the docs say "agent vision" or "agent browser tool", it uses whatever the host provides — native image reading, a browser MCP, the project preview, or a user-supplied screenshot.

### Subjects and detail accuracy

- **Objects and characters.** Each subject is classified `object`, `character`, or `hybrid`. Objects follow the hard-surface pipeline; characters route through an anatomy-aware track (head-unit proportions, facial landmarks, pose) documented in `grimoire/character/reconstruction.md`.
- **Detail-first analysis.** Before code generation the pipeline enumerates a `detailInventory` of identity-defining small details (gloss, bevel/rounding, screws/rivets, engraved or painted linework, contours, stains and wear). Every detail must map to a real component or material entry, and a strict-quality gate blocks generation until the inventory is complete. Taxonomy: `grimoire/intake/detail_inventory.md`.
- **Maximum likeness for a specific person or character.** An opt-in projection-first path fits a parametric template to image landmarks, de-lights the photo, camera-matches the render, and projects the reference onto the mesh. A single image cannot guarantee 100 percent likeness, so the pipeline reports per-region confidence and asks for more views when it matters. Details: `grimoire/character/likeness_maximization.md`.
- **Multi-view silhouette carving.** An opt-in `geometryDescriptor.visualHull` intersects at least two deterministic orthographic binary silhouettes into a bounded, welded voxel mesh. It records unseen areas as low-confidence rather than inventing hidden detail. Schema and runtime check: `grimoire/scripts.md`.
- **CS2 weapon review gates.** Knife and Glock-18 routes use family-specific component contracts. The review records exactness tier, family identity, painted-region and projection coverage, per-region confidence, approximation notes, and versioned review-scene metadata; component-coverage and map-stripped blockout gates prevent a convincing texture from standing in for real structure. See [`docs/cs2/review-gates.md`](docs/cs2/review-gates.md).
- **Resumable local workflow.** `forge/state.py` records an ordered, evidence-backed intake/pass checklist for generic, character, and CS2 profiles. `forge/next.py --state` resumes from that checklist while the existing spec, render, and review gates remain authoritative.
- **Material reference pipeline.** Every visible material region can be cropped, analyzed, resolved against the versioned Three.js material registry, fitted into `ObjectSculptSpec`, rendered from controlled camera views, and accepted only after a per-region comparison gate. See [`docs/materials/README.md`](docs/materials/README.md).
- **Python-assisted browser rendering.** Python may orchestrate camera batches, hashes, manifests, and deterministic diagnostics, but the target browser Three.js route remains the rendering authority. See [`grimoire/build/python_threejs_render_bridge.md`](grimoire/build/python_threejs_render_bridge.md).

---

## How it works

A staged sculpting pipeline turns the reference image into a spec, then generates and vision-reviews one build pass at a time — `blockout → structural → form → material → surface → lighting → interaction → optimization` — self-correcting until every identity-defining feature clears its threshold.

**→ Full pipeline diagram, gates, self-correction logic, and the token-efficiency design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**
A staged sculpting pipeline turns the reference image into a spec, then generates and vision-reviews one build pass at a time — `blockout → structural → form → material → surface → lighting → interaction → optimization` — self-correcting until every identity-defining feature clears its threshold. Deterministic Python scripts handle validation and gating; model tokens are spent only on visual judgment and code.

**→ Full pipeline diagram, gates, self-correction logic, script reference, and the token-efficiency design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**

---

## Quick start

1. **Install** — place this folder in your skills directory:

   ```bash
   git clone https://github.com/img2threejs/img2threejs.git ~/.claude/skills/img2threejs
   ```

   If you use more than one host, keep a single checkout and point each entrypoint at it as a
   symlink, so they cannot drift apart:

   ```text
   ~/.claude/skills/img2threejs -> <your checkout>
   ~/.codex/skills/img2threejs  -> <your checkout>
   ```

2. **Invoke** — in Claude Code, attach or point to an object image and run:

   ```
   /img2threejs Rebuild this object as a Three.js model, keep the proportions, angles, and colours.
   ```

   That is enough: the skill classifies the subject, runs the detail inventory, and gates every pass on its own.

3. **Follow the pipeline** — the skill validates the image, writes an assessment and spec, generates the factory pass by pass, and shows you a side-by-side comparison at each step until the render matches.

   For a multi-session reconstruction, create a local state index first:

   ```bash
   python3 forge/state.py init --reference <image> --profile character --spec object-sculpt-spec.json
   python3 forge/next.py --state .img2threejs/state.json
   ```

### Driving it harder

The one-liner leaves the judgement calls to the skill. When you already know what "correct" means for your subject, say so — each line below maps onto a real gate or artifact in the pipeline, so it changes what gets enforced rather than just adding adjectives:

```
/img2threejs Rebuild the subject in this image as a procedural Three.js model.

Fidelity   Hold proportions and silhouette to the reference. Enumerate the identity-defining
           details first — bevels and rounding, panel seams, fasteners, engraved or painted
           linework, gloss vs matte zones, wear — and drop any detail you cannot place on a
           real component instead of faking it.
Materials  Derive the finish class and gradient stops from the reference pixels, not from
           memory. Flag any colour that will not survive tone-mapping.
Runtime    Expose pivots and sockets for whatever should move, plus a userData.tick for a
           looping idle animation.
Gates      Run --strict-quality, and do not advance a pass until the side-by-side review
           passes. Report per-region confidence for anything the image cannot show.
```

Useful additions depending on the subject:

- **A specific person or character** — `Maximize likeness: fit the parametric template to the landmarks, de-light and camera-match the reference, then project it. Tell me which regions are inferred.`
- **An animal or creature** — `This is a creature, not a humanoid — use the quadruped body plan and the body-unit proportion system.`
- **A saturated anodized or candy finish** — `The coat is candy-coat, not gem-metal. Keep the hue; do not let the environment steal it.`
- **A cost ceiling** — `Stay at low effort and skip the presentation composer; I only need the evaluation render.`

The scripts run from the skill root and need only Python 3.10+ — nothing to install.

```bash
python3 forge/stage1_intake/probe_image.py <image>
python3 forge/stage2_spec/new_pre_spec_assessment.py "Name" --image <image> --out assessment.json
python3 forge/stage2_spec/new_sculpt_spec.py "Name" --image <image> --assessment assessment.json --out spec.json
python3 forge/stage2_spec/validate_sculpt_spec.py spec.json --strict-quality
python3 forge/stage3_build/generate_threejs_factory.py spec.json --out src/createObjectModel.ts
```

The factory generator repeats the strict-quality gate and is fail-closed: on failure it returns
`BLOCKED` with the spec artifact, failure metrics, causes, and next action, and does not write a
factory. `--allow-nonstrict` is only for explicit legacy test fixtures, never production output.

### Copy-paste prompts for the GLB-reference route

Rebuilding a character from a **GLB reference** rather than a photo is a different route with its own
gates — the GLB is a measurement instrument and never ships. Three prompts cover it, each in its own
copy block:

| Prompt | Use it when | Do not use it when |
|---|---|---|
| [Build](docs/GLB_CHARACTER_PROMPT.md) | you have a GLB and no built surfaces yet | there is no GLB, or the build already completed and merely looks wrong |
| [Polish](docs/GLB_CHARACTER_POLISH_PROMPT.md) | the build completed and the result does not look like the GLB | the surfaces were never built — that is a build re-run, not a polish |
| [Animation](docs/GLB_CHARACTER_ANIMATION_PROMPT.md) | the figure looks right standing still and has passed the build gates | the surface is ungated, or `joint_loops.py` fails — that is a surface finding no weight tuning reaches |

They force every parameter the GLB genuinely carries — size, proportions, per-band widths and
centroids, base colour, roughness and metalness — to the measured value, with the check that proves
it landed. Three things are **not** 1:1 and each prompt says so: rigging and animation are usually
absent from the asset (`skinCount: 0`, `animationCount: 0`), texture images and normal maps are
deliberately not copied per this skill's code-only contract, and nothing finer than the node's cell
size can be carried at all.

**These are reference material, not a guarantee.** They are written to be general, so the measured
figures in them came from one character and are there to show what to measure — not values to copy.
Run the one that matches your situation rather than all three in sequence; each notes the cost of
being used out of turn.

For the script-by-script reference, the full scripts table, and expected artifacts, see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Why it is token-efficient

Most image-to-3D agent loops burn tokens by asking the model to do mechanical work — re-reading the whole model every pass, scoring pixels, validating JSON by hand, re-running steps it already did. img2threejs pushes all of that into deterministic scripts and spends model tokens only where judgment is actually required.

- **Scripts enforce, the model judges.** The Python scripts handle validation, gating, spec authoring, PBR extraction, comparison-sheet packaging, and pipeline state. They never score visuals. The model's tokens go to one thing: looking at a single side-by-side sheet and deciding pass or fail.
- **Zero dependencies, zero install churn.** Every script is pure Python 3.10+ standard library. No pip, no PIL, no numpy, no Playwright. PNG read/write is done with `struct` and `zlib`. Nothing to install means nothing to debug in-context.
- **Pass-gated generation.** The code generator emits only the currently unlocked build pass. The model does not regenerate or re-read the entire model on every iteration — each step is small and scoped.
- **Fail fast, before codegen.** A strict-quality gate blocks shallow specs before a single line of Three.js is generated, so you never spend tokens rendering a model that was underspecified from the start.
- **One image per review.** Each pass is judged from exactly one packaged comparison sheet (reference beside render), not a scattering of screenshots.
- **Text output, not binaries.** The result is diffable TypeScript plus a JSON spec — small, reviewable, and version-controllable, instead of multi-megabyte mesh files.

The net effect: you still get a faithful 3D model from an image, but the expensive model context is reserved for visual judgment and code, not bookkeeping. For the full per-stage and per-cycle token breakdown, see [docs/TOKEN_COST.md](docs/TOKEN_COST.md).

---

## Scripts

| Script | Role |
| --- | --- |
| `stage1_intake/probe_image.py` | Image metadata and obvious technical issues (not a visual check). |
| `stage2_spec/new_pre_spec_assessment.py` | Classify the object, score complexity, emit a quality contract. |
| `stage2_spec/new_sculpt_spec.py` | Author the ObjectSculptSpec from the assessment. |
| `stage2_spec/validate_sculpt_spec.py` | Validate the spec; `--strict-quality` blocks shallow specs before codegen. |
| `stage1_intake/extract_pbr_evidence.py` | Reference-derived PBR evidence per crop (inference, not inverse rendering). |
| `stage1_intake/material_region_analysis.py` | Crop material regions, run texture/PBR evidence, and resolve registry profiles. |
| `stage2_spec/apply_material_analysis.py` | Wire region assignments, priors, maps, and provenance into ObjectSculptSpec. |
| `stage3_build/orchestrate_passes.py` | Locked pass state: status, check, sync. |
| `stage3_build/generate_threejs_factory.py` | Emit the Three.js `Group` factory for the current unlocked pass. |
| `stage4_review/material_views.py` | Emit multi-angle, zoomed, microscope, environment, and capture-readback contracts. |
| `stage4_review/material_comparator.py` | Compare the visible material crop and classify per-channel mismatches. |
| `stage4_review/material_feedback.py` | Apply bounded, material-scoped corrections through the existing stop policy. |
| `stage4_review/material_gate.py` | Block material-pass until registry, crop, render, compatibility, and comparison evidence passes. |
| `stage4_review/make_comparison_sheet.py` | Package one reference-vs-render sheet for review. |
| `stage4_review/append_review.py` | Record a per-pass review: scores, decision, evidence. |
| `stage4_review/cs2_review.py` | Evaluate the blocking CS2 knife review contract and versioned scene thresholds. |
| `_shared/feature_acceptance_policy.py` | Internal helper enforcing per-feature score thresholds. |
| `stage1_intake/build_detail_inventory.py` | Slice the reference into zones and scaffold a detail inventory. |
| `stage1_intake/extract_landmarks.py` | Overlay a landmark grid and scaffold an anatomy block for characters. |
| `stage1_intake/solve_camera_pose.py` | Emit a reference-camera block so the render can be camera-matched. |
| `stage1_intake/delight_albedo.py` | Approximate a neutral albedo from the photo before texture projection. |
| `stage3_build/bake_projected_texture.py` | Emit a projection/UV-bake descriptor for photo-texture projection. |

| `stage5_rig/rig_spec.py` | Derive and validate a skeleton from the component tree, so bones cannot drift from the geometry. |
| `stage5_rig/geodesic_skinning.py` | Vertex weights from distance measured through the solid; keeps rigid roles out of smooth skinning. |
| `stage5_rig/validate_rig_payload.py` | Blocking payload-integrity gate before a `THREE.Skeleton` is bound. |
| `stage1_intake/extract_hair_evidence.py` | Hair/skin split, banded coverage, hairline, highlight band, root-to-tip delta. |
| `stage4_review/scalp_exposure.py` | HARD gate: finds bald patches on geometry, before any render. |
| `stage4_review/hair_gate.py` | Soft gate: hair coverage, hairline and highlight offsets against the reference. |
| `stage4_review/interior_difference.py` | Appearance difference inside the silhouette, banded by height. Required per visual pass. |
| `_shared/chirality.py` | Left/right as an importable convention, with the two gates the two chirality defects need. |
| `_shared/pipeline_routing.py` | Fail-closed weapon/character routing; low confidence resolves to `request-input`. |

This is a curated selection — `forge/` holds around ninety modules. The executable reference with every flag is [`grimoire/scripts.md`](grimoire/scripts.md), and the gate-by-gate contract is [`grimoire/review/gates_reference.md`](grimoire/review/gates_reference.md). The rest of `grimoire/` holds the rubrics each gate applies (validation, pre-spec assessment, procedural patterns, material and lighting realism, attachment correctness, action-ready models, self-correction).

### Optional reference-fidelity tooling

The stdlib-only core can use an isolated evidence layer without taking on runtime dependencies:
SAM2 component masks, Depth Anything V2 relative-depth priors, MediaPipe face/pose landmarks,
Chrome DevTools diagnostics, Three.js scene inspection, Playwright cross-browser fallback, and
version-aware Context7 retrieval. These tools never approve a pass or silently provide geometry.
Install, routing, provenance rules and exact commands:
[`docs/integrations/reference_fidelity_tooling.md`](docs/integrations/reference_fidelity_tooling.md).

### Optional GLB-baseline character pipeline

[`integrations/glb_character_pipeline`](integrations/glb_character_pipeline) reconstructs a character
from a **multipart GLB used as a measurement instrument plus its diffuse image**, and emits the
procedural TypeScript the demo actually ships — no `.glb` or `.bin` is fetched at runtime. It carries
its own `pyproject.toml`/`uv.lock` so the stdlib-only `forge` core stays dependency-free, and operates
on a companion showcase checkout via `IMG2THREEJS_SHOWCASE_ROOT`.

It applies **only** when a build has a GLB to measure — skip it entirely otherwise, and use the core
image-driven pipeline instead. Reproduces `girl-character`'s shipped `crossSections.ts` exactly (748
rings, 86,240 ring points). Method and per-stage rationale:
[`PIPELINE.md`](integrations/glb_character_pipeline/PIPELINE.md).

---

## What you get

- An `ObjectSculptSpec` JSON: the full component tree, materials, repetition systems, sockets, and a recorded review history for every pass.
- A TypeScript `createObjectNameModel(spec, options)` factory returning a `THREE.Group`, with `root.userData.sculptRuntime` exposing nodes, sockets, colliders, and destruction groups.
- For character builds, `root.userData.rig`: bones, one shared `Skeleton`, bone order and index map, and a `bound` flag computed from whether every skinned mesh actually bound.
- A render plus comparison sheets documenting the fidelity at each pass.
For the script-by-script reference and the full list of output artifacts, see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Roadmap

**Shipped:**

- **v1.0** — object pipeline: staged sculpt, render-vs-reference review loop, action-ready hierarchy.
- **v1.1** — detail-first analysis: required detail inventory, strict-quality gate.
- **v1.2** — humanoid character generator: anatomy track, proportion-lock and feature-placement passes.
- **v1.3** — quality & efficiency: the Divine Eye deterministic review harness, input-integrity and geometry-truth gates, reference-grounded texture and gradient analysis, CIEDE2000 colour math.
- **v1.4 — The Weapon Update** — CS2 image-matched reconstruction: provenance-aware intake, projection-first finishes, family-specific weapon adapters, and structural review gates.
- **v1.4.1** — CS2 hardening: explicit component coverage, a dedicated Glock-18 assembly contract, map-stripped blockout evidence, and stricter geometry-integrity checks.
- **creature generator** — 4 body plans (quadruped / avian / winged-dragon / serpentine), `animalAnatomy` spec, spine-loft geometry, ΔE00 colour gates.
- **v1.5 — The Character Update** — a skeleton derived from the component tree and bound to `SkinnedMesh` geometry, geodesic skinning, hair as a five-stage subsystem with a hard scalp-exposure gate, chirality gates, interior-difference review, the `tapered-sweep` primitive, the material pipeline with a blocking acceptance gate, and resumable workflow state. Not included: the `hairProfile` compiler, IK, pose-sweep gating, clothing.

**Next — one theme per release:**
- **v1.6 — The Environment Update**: buildings, rooms, streets, vegetation, terrain-aware and multi-object reconstruction.
- **v1.7 — The Game Pipeline Update**: Unity and Unreal exporters, a Blender bridge, LOD and collision-mesh generation.
- **v1.8 — The Animation Update**: auto rigging, auto skin weights, Mixamo compatibility, facial rig.
- **v1.9 — The AI Studio Update**: web UI, batch processing, visual prompt builder, cloud rendering.
- **v2.0 — The Procedural World Update**: multi-view reconstruction, procedural city generation, semantic world understanding, plugin ecosystem and API.

The arc: assets (v1.4–v1.5) → worlds (v1.6–v1.7) → production (v1.8–v1.9) → an AI game-asset platform that generates playable worlds from reference images (v2.0).

**→ Full roadmap** — per-version detail, the four-phase long view, and the tracked capability gaps: **[ROADMAP.md](ROADMAP.md)**. Technical specification: [docs/UPGRADE_PLAN.md](docs/UPGRADE_PLAN.md).

---

## Honesty about limits

A single image cannot reveal hidden sides or guarantee exact geometry. The skill states plainly when output is approximate, stylized, or low-poly, and infers unseen faces by mirroring visible ones rather than faking confidence. It is strong for hard-surface objects; characters are stylized reconstructions, not photoreal likeness. "This cannot reach the requested fidelity from this image" is a valid, expected result.

---

## Star history

If img2threejs is useful to you, a star helps others find it.

<a href="https://www.star-history.com/#hoainho/img2threejs&Timeline">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=hoainho/img2threejs&type=timeline&theme=dark&legend=top-left&sealed_token=HhzHOwb32twyQntl75HMLNf5E7hkH9aNTaSpn20ZThsyQC2Rt7fA_Wthz0osSgItW_WUiwA3MUa5-7GXquQCVL1uHLePUOUN9uVoiArBCm-l21DXJ51yVQ" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=hoainho/img2threejs&type=timeline&legend=top-left&sealed_token=HhzHOwb32twyQntl75HMLNf5E7hkH9aNTaSpn20ZThsyQC2Rt7fA_Wthz0osSgItW_WUiwA3MUa5-7GXquQCVL1uHLePUOUN9uVoiArBCm-l21DXJ51yVQ" />
    <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=hoainho/img2threejs&type=timeline&legend=top-left&sealed_token=HhzHOwb32twyQntl75HMLNf5E7hkH9aNTaSpn20ZThsyQC2Rt7fA_Wthz0osSgItW_WUiwA3MUa5-7GXquQCVL1uHLePUOUN9uVoiArBCm-l21DXJ51yVQ" width="600" />
  </picture>
</a>

---

## Support the project

img2threejs is free and open source. If it saved you time or found its way into your project, consider supporting continued development:

[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/O8A625YLSR)

VietQR / MoMo / PayPal also work — see the [donate page](https://img2threejs.io/donate.html).

---

## Sponsors

Reconstruction-by-code is an inference workload before it is a graphics one: every gate rerun, every
render-vs-reference pass and every material fit spends tokens. These three pay for that loop.

<table>
  <tr>
    <td align="center" width="200">
      <a href="https://www.atlascloud.ai/console/coding-plan" target="_blank" rel="noopener noreferrer">
        <picture>
          <source media="(prefers-color-scheme: dark)" srcset="assets/sponsors/atlas-cloud-logomark-white.svg" />
          <source media="(prefers-color-scheme: light)" srcset="assets/sponsors/atlas-cloud-logomark-black.svg" />
          <img alt="Atlas Cloud" src="assets/sponsors/atlas-cloud-logomark-black.svg" width="72" height="67" />
        </picture>
      </a>
      <br /><sub><b>Atlas Cloud</b></sub>
      <br /><sub>Full-modal AI inference</sub>
    </td>
    <td align="center" width="200">
      <a href="https://www.tripo3d.ai/" target="_blank" rel="noopener noreferrer">
        <picture>
          <source media="(prefers-color-scheme: dark)" srcset="assets/sponsors/tripo-logomark-white.svg" />
          <source media="(prefers-color-scheme: light)" srcset="assets/sponsors/tripo-logomark-black.svg" />
          <img alt="Tripo" src="assets/sponsors/tripo-logomark-black.svg" width="68" height="68" />
        </picture>
      </a>
      <br /><sub><b>Tripo</b></sub>
      <br /><sub>Image &amp; text to 3D</sub>
    </td>
    <td align="center" width="200">
      <a href="https://hyper3d.ai/" target="_blank" rel="noopener noreferrer">
        <picture>
          <source media="(prefers-color-scheme: dark)" srcset="assets/sponsors/hyper3d-logomark-white.png" />
          <source media="(prefers-color-scheme: light)" srcset="assets/sponsors/hyper3d-logomark-black.png" />
          <img alt="Hyper3D" src="assets/sponsors/hyper3d-logomark-black.png" width="68" height="68" />
        </picture>
      </a>
      <br /><sub><b>Hyper3D</b></sub>
      <br /><sub>Rodin generative 3D</sub>
    </td>
  </tr>
</table>

### Atlas Cloud

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/sponsors/atlas-cloud-logomark-white.svg" />
  <source media="(prefers-color-scheme: light)" srcset="assets/sponsors/atlas-cloud-logomark-black.svg" />
  <img alt="" src="assets/sponsors/atlas-cloud-logomark-black.svg" width="38" height="36" align="right" />
</picture>

**[Atlas Cloud](https://www.atlascloud.ai/console/coding-plan)** is a full-modal AI inference
platform: one AI API for video generation, image generation and LLM access, with unified access to
300+ curated models across every modality instead of a separate integration per vendor.

That single endpoint is what keeps this project's loop affordable — the pipeline is token-hungry by
design, because gating a spec before codegen means running the analysis more than once. Atlas Cloud's
coding plan is the budget-friendly route to that API.

**→ [Open the Atlas Cloud coding plan](https://www.atlascloud.ai/console/coding-plan)**

### Tripo

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/sponsors/tripo-logomark-white.svg" />
  <source media="(prefers-color-scheme: light)" srcset="assets/sponsors/tripo-logomark-black.svg" />
  <img alt="" src="assets/sponsors/tripo-logomark-black.svg" width="38" height="38" align="right" />
</picture>

**[Tripo](https://www.tripo3d.ai/)** turns a prompt or a reference image into a production 3D asset:
High Detail meshes up to 2M polygons for render and print, artist-grade quad **Smart Mesh** from 500
to 50K triangles for real-time engines, AI auto-rigging, PBR texturing up to 8K, and part-level
segmentation. Exports GLB, FBX, OBJ, USD, STL and 3MF, with first-party plugins for Blender, Unity,
Unreal, Godot, Cocos and ComfyUI.

It pairs with img2threejs as the measuring stick. A procedural rebuild lives or dies on silhouette,
proportion and joint placement, and a quad mesh plus an auto-rig of the same subject gives those
gates a second read that one reference photograph cannot settle on its own.

**→ [Open Tripo Studio](https://www.tripo3d.ai/)**

### Hyper3D

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/sponsors/hyper3d-logomark-white.png" />
  <source media="(prefers-color-scheme: light)" srcset="assets/sponsors/hyper3d-logomark-black.png" />
  <img alt="" src="assets/sponsors/hyper3d-logomark-black.png" width="38" height="38" align="right" />
</picture>

**[Hyper3D](https://hyper3d.ai/)** builds **Rodin**, which generates a 3D asset from a prompt or an
image in seconds with high reference fidelity and coherent detail across views. Generation is
steerable rather than a dice roll: bounding-box, voxel and point-cloud ControlNet guidance, partial
editing to refine one region without disturbing the rest, smart low-poly optimisation, and
ChatAvatar for rigged production faces. Exports STL, FBX, OBJ, GLB, glTF and USDZ.

It answers the one question a single photograph never can — what the back looks like. Generate the
subject, orbit it, and the hidden sides become references the material and surface gates can actually
be run against, instead of assumptions the pipeline has to make silently.

**→ [Open Hyper3D Rodin](https://hyper3d.ai/)**

---

Sponsorship pays for compute, not for coverage: a sponsor's product is described here in its own
terms, and no gate, default or benchmark in this repository is weighted toward one. Want your logo in
this row? Open an issue or write to <hoainho.work@gmail.com>.

---

## Contributing

Contributions are welcome — procedural material recipes, new gates, host coverage, and demos especially. See [CONTRIBUTING.md](CONTRIBUTING.md) and the [roadmap](ROADMAP.md) for where the project is headed.

## License

Apache License 2.0. See [LICENSE](LICENSE).
