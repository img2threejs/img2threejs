"""Owned synthetic pipeline receipts; no Blender, network, or visual qualification."""
from pathlib import Path
import json
import _common as C
from textured_support import write_png
from workflow_state import new_state, mark_steps, save_state

def pipeline(workspace, generation=1, *, accepted=False, write_state=True):
    workspace = Path(workspace)
    directory = C.gen_dir(workspace, generation)
    (directory / "raw").mkdir(parents=True, exist_ok=True)
    (directory / "renders").mkdir(exist_ok=True)
    reference = write_png(workspace / "reference.png", 16, 24, (3, 3, 3))
    asset = directory / "raw/model.fbx"
    asset.write_bytes(b"owned synthetic model")
    glb = directory / "normalized.glb"
    glb.write_bytes(b"owned synthetic normalized GLB")
    render = write_png(directory / "renders/front.png", 16, 24, (1, 1, 1))
    sheet = write_png(directory / "sheet-front.png", 280, 280, (2, 2, 2))
    rel = lambda path: C.workspace_relative(workspace, path)
    C.write_json(directory / "generate.json", C.receipt("generate", generation=generation,
        input={"path": str(reference), "sha256": C.sha256_file(reference)},
        generator={"tool": "fixture", "model": "fixture", "scenario": "film", "prompt": "", "taskId": "T1", "credits": 65, "then": ""},
        generationLimit=4, stateMaxGenerations=4,
        asset={"path": "raw/model.fbx", "format": "fbx", "bytes": asset.stat().st_size, "sha256": C.sha256_file(asset)}))
    C.write_json(directory / "inspect.json", C.receipt("inspect", generation=generation,
        facts={"source": rel(asset), "sha256": C.sha256_file(asset), "bpy": "fixture", "triangles": 10,
               "vertices": 8, "meshes": 1, "materialCount": 1, "textureCount": 1, "uvSets": ["UVMap"],
               "textures": [{"name": "Color", "width": 64, "height": 64}], "islands": {"count": 1},
               "nonManifoldEdges": {"raw": 5, "welded": 1}, "skeleton": {"bones": 0, "actions": 0}}))
    C.write_json(directory / "normalize.json", C.receipt("normalize", generation=generation,
        source=rel(asset), sourceSha256=C.sha256_file(asset), output=rel(glb),
        normalization={"bbox": {"size": [0.5, 0.4, 1.8]}, "speckRemoval": {"removedIslands": 2}},
        export={"bytes": glb.stat().st_size, "sha256": C.sha256_file(glb)}))
    C.write_json(directory / "render.json", C.receipt("render", generation=generation, glb=rel(glb),
        glbSha256=C.sha256_file(glb), views=[{"path": rel(render), "sha256": C.sha256_file(render)}]))
    C.write_json(sheet.with_suffix(".json"), C.receipt("sheet", generation=generation, output=rel(sheet),
        panels=[{"path": rel(p), "sha256": C.sha256_file(p)} for p in (reference, render)], sha256=C.sha256_file(sheet)))
    files = [asset, glb, render, sheet, sheet.with_suffix(".json")]
    files += [directory / (name + ".json") for name in ("generate", "inspect", "normalize", "render")]
    bindings = {"reference": {"path": str(reference), "sha256": C.sha256_file(reference)},
                "files": {rel(path): C.sha256_file(path) for path in files}}
    C.write_json(directory / "likeness.json", C.receipt("likeness", generation=generation, kind="generic",
        decider="jev-1.14.0", provider="jev", presentedAsJev=True,
        sheet={"path": rel(sheet), "sha256": C.sha256_file(sheet), "size": [280, 280]}, bindings=bindings,
        summary={"overall": {"verdict": "same", "probabilities": {"same": 1, "similar": 0, "different": 0}},
                 "meanScore": 3.0, "scores": {"silhouette": 3.0}, "defects": {"melted": 0.2},
                 "flaggedDefects": [], "defectFlagThreshold": 0.5}))
    state = new_state(str(reference), profile="textured", max_generations=4)
    state["textured"]["generation"] = generation
    for step in ("reference-admission", "generate", "inspect", "normalize", "render", "likeness"):
        mark_steps(state, [step], status="done", evidence=["owned fixture"])
    if write_state:
        save_state(workspace / ".img2threejs/state.json", state)
    if accepted:
        import decide
        decide.main(["--workspace", str(workspace)])
    return directory, reference, bindings


def model_grant(testcase, workspace, reference, model_id="fixture", run_id="fixture-run"):
    """Only credential status is synthetic; confirmation, store and journal are real."""
    from contextlib import redirect_stdout
    import io
    import os
    import pty
    import tempfile
    from unittest.mock import patch
    import route_no_account
    home = tempfile.TemporaryDirectory()
    testcase.addCleanup(home.cleanup)
    environment = patch.dict(os.environ, {"IMG2_HOME": str(Path(home.name).resolve())})
    environment.start()
    testcase.addCleanup(environment.stop)
    credentials = patch.object(route_no_account, "credential_status", return_value="credential-missing")
    credentials.start()
    testcase.addCleanup(credentials.stop)
    master, slave = pty.openpty()
    testcase.addCleanup(os.close, master)
    testcase.addCleanup(os.close, slave)
    os.write(master, f"attest {run_id} {model_id}\n".encode())
    terminal = patch.object(route_no_account, "TTY_PATH", os.ttyname(slave))
    terminal.start()
    testcase.addCleanup(terminal.stop)
    with redirect_stdout(io.StringIO()):
        route_no_account.attest(workspace, run_id, model_id=model_id,
            images=[C.workspace_relative(workspace, reference)], token_budget=64000, purposes=["quality-review"])
    return run_id


def record_model(testcase, workspace, generation=1, *, kind="generic", verdict="similar", model_id="fixture"):
    import likeness
    directory = C.gen_dir(workspace, generation)
    reference = Path(C.read_json(directory / "generate.json")["input"]["path"])
    run_id = model_grant(testcase, workspace, reference, model_id)
    # Remove only the synthetic pre-existing fixture verdict, not a produced receipt.
    (directory / "likeness.json").unlink()
    questions = likeness.build_questions(kind)
    answers = {key: (verdict if row["type"] == "choice" else 3 if row["type"] == "score" else 0.1)
               for key, row in questions.items()}
    answer_path = Path(workspace) / "answers.json"
    C.write_json(answer_path, answers)
    likeness.main(["record-model-answers", "--workspace", str(workspace), "--gen", str(generation),
        "--kind", kind, "--answers", str(answer_path), "--model-id", model_id,
        "--run-id", run_id, "--reference", str(reference)])
    return run_id
