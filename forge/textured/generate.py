#!/usr/bin/env python3
"""Record one generation made by the host's MCP generator (an AGENT step: the tool call itself is not
made here). Stores the raw model under .img2threejs/textured/gen-N/raw/, with provenance: task id,
credits, model, scenario, prompt and the sha256 of the input image.

    generate.py record --workspace . --gen 2 --input ref.png --task-id T --model tripo-p1 \\
        --scenario film --credits 60 --model-file out/model.fbx [--model-dir out/] [--prompt ...]
    generate.py status --workspace .

A regeneration (gen > 1) MUST differ from every earlier generation in model, scenario, prompt or
input image; the generator is not deterministic-with-feedback, so re-sending the same request is paid
noise, and is refused. ``--max-generations`` caps the loop (default 4, or the state file's value).
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

MODEL_SUFFIXES = (".glb", ".gltf", ".fbx", ".obj")
KNOWN_MODELS = ("tripo-v3.1", "tripo-p1", "tripo-p2")
DEFAULT_MAX_GENERATIONS = 4


def signature(record):
    return (record["input"]["sha256"], record["generator"]["model"], record["generator"]["scenario"],
            record["generator"].get("prompt") or "")


def previous_generations(workspace, before):
    rows = []
    for number in range(1, before):
        path = C.gen_dir(workspace, number) / "generate.json"
        if path.is_file():
            rows.append((number, C.read_json(path)))
    return rows


def copy_asset(model_file, model_dir, raw):
    """Copy the model, and when it is not self-contained (fbx/gltf/obj) its sibling files (textures)."""
    raw.mkdir(parents=True, exist_ok=True)
    if model_dir:
        source_dir = Path(model_dir).resolve()
        if not source_dir.is_dir():
            raise C.TexturedError(f"--model-dir is not a directory: {source_dir}")
        for item in sorted(source_dir.rglob("*")):
            if item.is_file() and not any(part.startswith(".") for part in item.relative_to(source_dir).parts):
                target = raw / item.relative_to(source_dir)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)
        relative = model_file.resolve().relative_to(source_dir) if source_dir in model_file.resolve().parents else Path(model_file.name)
        if not (raw / relative).is_file():
            shutil.copy2(model_file, raw / relative)
        return raw / relative
    target = raw / model_file.name
    shutil.copy2(model_file, target)
    return target


def cmd_record(args, workspace):
    state_path = Path(args.state) if args.state else workspace / ".img2threejs" / "state.json"
    limit = args.max_generations
    if limit is None and state_path.is_file():
        limit = (C.read_json(state_path).get("textured") or {}).get("maxGenerations")
    limit = limit or DEFAULT_MAX_GENERATIONS
    if args.gen > limit:
        raise C.TexturedError(f"generation {args.gen} exceeds the cap of {limit}; pick the best of the existing ones instead")
    model_file = Path(args.model_file).expanduser().resolve()
    image = Path(args.input).expanduser().resolve()
    for path, what in ((model_file, "--model-file"), (image, "--input")):
        if not path.is_file():
            raise C.TexturedError(f"{what} not found: {path}")
    if model_file.suffix.lower() not in MODEL_SUFFIXES:
        raise C.TexturedError(f"--model-file must be one of {MODEL_SUFFIXES}")
    if args.credits < 0:
        raise C.TexturedError("--credits must be >= 0")
    if not args.task_id.strip() or not args.model.strip() or not args.scenario.strip():
        raise C.TexturedError("--task-id, --model and --scenario are required provenance")
    directory = C.gen_dir(workspace, args.gen)
    if (directory / "generate.json").exists() and not args.overwrite:
        raise C.TexturedError(f"{directory / 'generate.json'} already exists (use --overwrite to replace it)")
    expected = len(previous_generations(workspace, args.gen)) + 1
    if args.gen != expected:
        raise C.TexturedError(f"generations are recorded in order; the next one is {expected}")
    record = {
        "input": {"path": str(image), "sha256": C.sha256_file(image)},
        "generator": {"tool": args.tool, "model": args.model, "scenario": args.scenario, "prompt": args.prompt or "",
                      "taskId": args.task_id, "credits": args.credits, "then": args.then or ""},
    }
    for number, earlier in previous_generations(workspace, args.gen):
        if signature(earlier) == signature(record):
            raise C.TexturedError(
                f"generation {args.gen} repeats generation {number} (same input image, model, scenario and prompt); "
                "change at least one of them before spending credits again")
    asset = copy_asset(model_file, args.model_dir, directory / "raw")
    cumulative = args.credits + sum(row["generator"]["credits"] for _, row in previous_generations(workspace, args.gen))
    receipt = C.receipt("generate", generation=args.gen, input=record["input"], generator=record["generator"],
                        asset={"path": C.workspace_relative(directory, asset), "format": asset.suffix.lower().lstrip("."),
                               "bytes": asset.stat().st_size, "sha256": C.sha256_file(asset)},
                        creditsTotal=cumulative, note=args.note or "")
    C.write_json(directory / "generate.json", receipt)
    print(f"generate: recorded generation {args.gen} ({args.model}/{args.scenario}, task {args.task_id}, "
          f"{args.credits} credits, {cumulative} total) -> {directory / 'generate.json'}")
    return 0


def cmd_status(args, workspace):
    rows = []
    number = 1
    while (C.gen_dir(workspace, number) / "generate.json").is_file():
        row = C.read_json(C.gen_dir(workspace, number) / "generate.json")
        rows.append({"gen": number, **row["generator"]})
        number += 1
    print(__import__("json").dumps({"generations": rows, "creditsTotal": sum(r["credits"] for r in rows)}, indent=2))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(description="Record a generator run for the textured profile")
    commands = parser.add_subparsers(dest="command", required=True)
    record = commands.add_parser("record")
    record.add_argument("--workspace", default=".")
    record.add_argument("--state")
    record.add_argument("--gen", type=int, required=True)
    record.add_argument("--input", required=True, help="the image sent to the generator")
    record.add_argument("--task-id", required=True)
    record.add_argument("--model", required=True, help=f"generator model, e.g. {', '.join(KNOWN_MODELS)}")
    record.add_argument("--scenario", required=True, help="generator scenario preset, e.g. film, game-pc")
    record.add_argument("--credits", type=int, required=True)
    record.add_argument("--model-file", required=True)
    record.add_argument("--model-dir", help="directory holding the model and its textures (copied whole)")
    record.add_argument("--prompt", default="")
    record.add_argument("--then", default="", help="chained step the generator ran (e.g. texture)")
    record.add_argument("--tool", default="mcp__tripo_make")
    record.add_argument("--note")
    record.add_argument("--max-generations", type=int)
    record.add_argument("--overwrite", action="store_true")
    status = commands.add_parser("status")
    status.add_argument("--workspace", default=".")
    return parser


def main(argv):
    args = build_parser().parse_args(argv)
    workspace = C.resolve_workspace(args.workspace)
    return cmd_record(args, workspace) if args.command == "record" else cmd_status(args, workspace)


if __name__ == "__main__":
    C.run_cli(main)
