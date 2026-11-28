#!/usr/bin/env python3
"""Assemble the delivery for the accepted generation under .img2threejs/textured/final/:
the normalized GLB, its renders, the reference|render comparison sheet and a REPORT.md that states
the facts (generator provenance, geometry/texture facts, likeness with its decider) and what is NOT done.

The accepted generation comes only from the canonical workflow state; ``--gen`` must agree.
Nothing here claims more than the receipts say: model answers are never presented as Jev.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or os.getcwd()) != _HERE]
sys.path.append(_HERE)

import argparse  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402
import secrets  # noqa: E402
from pathlib import Path  # noqa: E402

import _common as C  # noqa: E402
import _chain as chain  # noqa: E402
from workflow_state import load_state, WorkflowStateError  # noqa: E402


def accepted_generation(workspace, state_path, explicit):
    try:
        state = load_state(C.workspace_state(workspace, state_path))
    except WorkflowStateError as error:
        raise C.TexturedError(str(error)) from None
    block = state.get("textured") or {}
    accepted = block.get("accepted")
    if (type(accepted) is not int or not any(row.get("outcome") == "accept" for row in block.get("history", []))
            or not isinstance(block.get("acceptedBinding"), dict)):
        raise C.TexturedError("the workflow has no bound accepted generation; run decide.py")
    if explicit is not None and explicit != accepted:
        raise C.TexturedError("--gen must match the workflow accepted generation")
    return accepted


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
    """(credits over every receipt ever written, superseded ones included; number of generations)."""
    receipts = C.generation_receipts(workspace)
    number = 0
    while (C.gen_dir(workspace, number + 1) / "generate.json").is_file():
        number += 1
    return sum(row["generator"]["credits"] for row in receipts), number


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
    state_path = C.workspace_state(workspace, args.state)
    generation = accepted_generation(workspace, state_path, args.gen)
    directory, parts = collect(workspace, generation)
    accepted = C.read_json(state_path)["textured"]["acceptedBinding"]
    if chain.judged(workspace, generation) != accepted:
        raise C.TexturedError("accepted evidence changed; rerun the affected stages and decide.py")
    reference = Path(args.reference).expanduser().resolve()
    if (str(reference) != accepted["bindings"]["reference"]["path"] or not reference.is_file()
            or C.sha256_file(reference) != accepted["bindings"]["reference"]["sha256"]):
        raise C.TexturedError("delivery reference does not match the accepted reference")
    sheet = directory / f"sheet-{C.safe_name(args.view, '--view')}.png"
    if C.workspace_relative(workspace, sheet) != parts["likeness"]["sheet"]["path"]:
        raise C.TexturedError("--view must match the judged comparison sheet")
    sources = chain.render_sources(workspace, generation, parts["render"])
    final = C.textured_root(workspace) / "final"
    if final.is_symlink() or (final.exists() and not final.is_dir()):
        raise C.TexturedError("final must be an owned delivery directory, not a link or file")
    names = []
    with tempfile.TemporaryDirectory(prefix=".delivery-stage-", dir=final.parent) as temporary:
        stage = Path(temporary)
        (stage / "renders").mkdir()
        def copy(source, relative):
            expected = (accepted["bindings"]["reference"]["sha256"] if source == reference else
                        accepted["bindings"]["files"][C.workspace_relative(workspace, source)])
            target = stage / relative
            shutil.copy2(source, target)
            if C.sha256_file(target) != expected:
                raise C.TexturedError("delivery input changed while copying")
            names.append(relative)
        copy(parts["glb"], "model.glb")
        for source in sources:
            copy(source, f"renders/{source.name}")
        copy(sheet, "comparison.png")
        copy(reference, f"reference{reference.suffix.lower()}")
        for name in ("generate", "inspect", "normalize", "render"):
            copy(directory / f"{name}.json", f"{name}.json")
        likeness = directory / "likeness.json"
        shutil.copy2(likeness, stage / likeness.name)
        if C.sha256_file(stage / likeness.name) != accepted["likenessSha256"]:
            raise C.TexturedError("accepted likeness changed while copying")
        names += ["likeness.json", "REPORT.md"]
        write_report(stage, workspace, generation, parts, args.reference, {"glb": "model.glb", "list": names})
        C.write_json(stage / "delivery.json", C.receipt(
            "delivery", generation=generation, glb=C.workspace_relative(workspace, final / "model.glb"),
            sha256=C.sha256_file(stage / "model.glb"), decider=parts["likeness"]["decider"], files=names,
            acceptedBinding=accepted))
        if chain.judged(workspace, generation) != accepted or C.read_json(state_path)["textured"].get("acceptedBinding") != accepted:
            raise C.TexturedError("accepted evidence changed while staging delivery")
        backup = final.parent / (".delivery-backup-" + secrets.token_hex(16))
        previous = final.exists()
        if previous:
            os.replace(final, backup)
        try:
            os.replace(stage, final)
        except BaseException:
            if previous:
                os.replace(backup, final)
            raise
        if previous:
            shutil.rmtree(backup)
    print(f"deliver: generation {generation} -> {final} ({len(names)} files)")
    return 0


if __name__ == "__main__":
    C.run_cli(main)
