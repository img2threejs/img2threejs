#!/usr/bin/env python3
"""Assemble the delivery for the accepted generation under .img2threejs/textured/final/:
the normalized GLB, its renders, the reference|render comparison sheet and a REPORT.md that states
the facts (generator provenance, geometry/texture facts, likeness with its decider) and what is NOT done.

The accepted generation comes from the workflow state (``textured.accepted``) or ``--gen``. Nothing
here claims more than the receipts say: a model-answered likeness is reported as such, never as Jev.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or os.getcwd()) != _HERE]
sys.path.append(_HERE)

import argparse  # noqa: E402
import shutil  # noqa: E402
from pathlib import Path  # noqa: E402

import _common as C  # noqa: E402


def accepted_generation(workspace, state_path, explicit):
    if explicit is not None:
        return explicit
    if state_path.is_file():
        accepted = (C.read_json(state_path).get("textured") or {}).get("accepted")
        if accepted:
            return int(accepted)
        raise C.TexturedError("the workflow has no accepted generation yet; run decide.py (or pass --gen)")
    raise C.TexturedError("no state file; pass --gen N")


def collect(workspace, generation):
    directory = C.gen_dir(workspace, generation)
    needed = {"generate": directory / "generate.json", "normalize": directory / "normalize.json",
              "render": directory / "render.json", "likeness": directory / "likeness.json",
              "inspect": directory / "inspect.json", "glb": directory / "normalized.glb"}
    missing = [name for name, path in needed.items() if not path.is_file()]
    if missing:
        raise C.TexturedError(f"generation {generation} is incomplete: missing {', '.join(missing)}")
    return directory, {name: (C.read_json(path) if path.suffix == ".json" else path) for name, path in needed.items()}


def credits_total(workspace):
    total, number = 0, 1
    while (C.gen_dir(workspace, number) / "generate.json").is_file():
        total += C.read_json(C.gen_dir(workspace, number) / "generate.json")["generator"]["credits"]
        number += 1
    return total, number - 1


def write_report(final, workspace, generation, parts, reference, files):
    gen, norm, likeness, facts = parts["generate"], parts["normalize"], parts["likeness"], parts["inspect"]["facts"]
    g, summary = gen["generator"], likeness["summary"]
    total, count = credits_total(workspace)
    scores = "\n".join(f"| {k} | {v:.2f} |" for k, v in summary["scores"].items())
    defects = "\n".join(f"| {k} | {v:.2f} |" for k, v in summary["defects"].items())
    decider = likeness["decider"]
    honesty = ("Answered by Jev under per-run consent; Jev is noisy (differences below about 0.15 are noise) and is "
               "advisory evidence, not acceptance authority." if likeness["presentedAsJev"] else
               f"Answered by the host model (`{decider}`), NOT by Jev: no Jev account/consent was available. "
               "These scores are not comparable with Jev scores and measure nothing independent of the model that built the loop.")
    size = norm["normalization"]["bbox"]["size"]
    text = f"""# Textured model report

Deliverable: `{files['glb']}` — a generated mesh with baked textures (not procedural or code-built).
Reference: `{reference}`.

## 1. Generation
| field | value |
|---|---|
| tool | {g['tool']} |
| model / scenario | {g['model']} / {g['scenario']}{(' / then ' + g['then']) if g['then'] else ''} |
| task id | `{g['taskId']}` |
| credits (this generation / all {count} generation(s)) | {g['credits']} / {total} |
| prompt | {g['prompt'] or '(none)'} |
| raw model | `{gen['asset']['path']}` ({gen['asset']['format']}, {gen['asset']['bytes']} bytes) |
| input image sha256 | `{gen['input']['sha256'][:16]}…` |

## 2. Facts (bpy {facts['bpy']}, receipts `inspect.json`, `normalize.json`)
| property | value |
|---|---|
| triangles / vertices | {facts['triangles']} / {facts['vertices']} |
| meshes / materials / textures | {facts['meshes']} / {facts['materialCount']} / {facts['textureCount']} |
| texture sizes | {', '.join(f"{t['name']} {t['width']}x{t['height']}" for t in facts['textures'])} |
| UV sets | {', '.join(facts['uvSets']) or 'none'} |
| connectivity islands (position-welded) | {facts['islands']['count']} (speck islands removed by normalize: {norm['normalization']['speckRemoval']['removedIslands']}) |
| non-manifold edges raw / welded | {facts['nonManifoldEdges']['raw']} / {facts['nonManifoldEdges']['welded']} (raw is inflated by glTF UV-seam splits) |
| skeleton | {facts['skeleton']['bones']} bones, {facts['skeleton']['actions']} actions |
| normalized extent (x, y, z) m | {size[0]:.3f}, {size[1]:.3f}, {size[2]:.3f} (feet at z=0, +Z up facing -Y in Blender space; the GLB is Y-up) |
| delivered GLB | {norm['export']['bytes']} bytes, sha256 `{norm['export']['sha256'][:16]}…` |

## 3. Likeness (generation {generation}, decider `{decider}`)
Overall verdict: **{summary['overall']['verdict']}** ({', '.join(f'{k} {v:.2f}' for k, v in summary['overall']['probabilities'].items())}); mean of {len(summary['scores'])} scores (0 to 4): **{summary['meanScore']}**.

{honesty}

| feature | score 0 to 4 |
|---|---|
{scores}

| defect | P(defect) |
|---|---|
{defects}

Flagged defects (P >= {summary['defectFlagThreshold']}): {', '.join(summary['flaggedDefects']) or 'none'}.

## 4. Files
{chr(10).join('- `' + name + '`' for name in files['list'])}

## 5. What is NOT done
- Not rigged, skinned or animated unless a domain rig track ran after this report; the pose is whatever the generator produced.
- Not retopologised or LOD'd; materials come from the generator's bake as one mesh. Not validated in a game engine or with gltf-validator.
- Back and sides are invented by the generator from the reference; a single image cannot show them.
- Likeness is a measurement by `{decider}` of one front render sheet; a human look at the renders is still the better check.
- The procedural visual gate in this repository's CLAUDE.md belongs to the code-built pipeline and was not run.
"""
    (final / "REPORT.md").write_text(text, encoding="utf-8")


def build_parser():
    parser = argparse.ArgumentParser(description="Assemble the textured delivery")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--state")
    parser.add_argument("--gen", type=int)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--view", default="front")
    return parser


def main(argv):
    args = build_parser().parse_args(argv)
    workspace = C.resolve_workspace(args.workspace)
    state_path = Path(args.state) if args.state else workspace / ".img2threejs" / "state.json"
    generation = accepted_generation(workspace, state_path, args.gen)
    directory, parts = collect(workspace, generation)
    final = C.textured_root(workspace) / "final"
    if final.exists():
        shutil.rmtree(final)
    (final / "renders").mkdir(parents=True)
    reference = Path(args.reference).expanduser().resolve()
    if not reference.is_file():
        raise C.TexturedError(f"reference not found: {reference}")
    names = []
    glb = final / "model.glb"
    shutil.copy2(parts["glb"], glb)
    names.append("model.glb")
    for row in parts["render"]["views"]:
        source = workspace / row["path"]
        if not source.is_file():
            raise C.TexturedError(f"render missing: {source}")
        shutil.copy2(source, final / "renders" / source.name)
        names.append(f"renders/{source.name}")
    sheet = directory / f"sheet-{C.safe_name(args.view, '--view')}.png"
    if not sheet.is_file():
        raise C.TexturedError(f"comparison sheet missing: {sheet}")
    shutil.copy2(sheet, final / "comparison.png")
    names.append("comparison.png")
    shutil.copy2(reference, final / f"reference{reference.suffix.lower()}")
    names.append(f"reference{reference.suffix.lower()}")
    for receipt in ("generate", "inspect", "normalize", "render", "likeness"):
        shutil.copy2(directory / f"{receipt}.json", final / f"{receipt}.json")
        names.append(f"{receipt}.json")
    names.append("REPORT.md")
    write_report(final, workspace, generation, parts, args.reference, {"glb": "model.glb", "list": names})
    C.write_json(final / "delivery.json", C.receipt(
        "delivery", generation=generation, glb=C.workspace_relative(workspace, glb), sha256=C.sha256_file(glb),
        decider=parts["likeness"]["decider"], files=names))
    print(f"deliver: generation {generation} -> {final} ({len(names)} files)")
    return 0


if __name__ == "__main__":
    C.run_cli(main)
