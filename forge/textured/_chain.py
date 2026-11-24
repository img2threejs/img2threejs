"""Fresh, local source-to-verdict bindings shared by judging, deciding and delivery."""
from pathlib import Path
import unicodedata
import _common as C


def _require(condition, description):
    if not condition:
        raise C.TexturedError(f"stale or incomplete textured evidence: {description}; rerun the affected stages")


def _file(root, path):
    path = Path(path)
    _require(path.is_file() and not path.is_symlink() and root in path.parents
             and path.resolve() == path, "file must remain inside its own generation")
    return path


def render_sources(workspace, generation, record):
    directory = C.gen_dir(workspace, generation)
    _require(directory.resolve() == directory, "generation root changed")
    rows = record.get("views")
    _require(type(rows) is list and bool(rows), "render views missing")
    sources, names = [], set()
    for row in rows:
        _require(type(row) is dict and type(row.get("path")) is str, "invalid render path")
        relative = Path(row["path"])
        _require(not relative.is_absolute() and ".." not in relative.parts and "\\" not in row["path"], "render path is not relative")
        source = _file(directory / "renders", Path(workspace) / relative)
        name = unicodedata.normalize("NFC", source.name).casefold()
        _require(name not in names and source.suffix.lower() == ".png", "duplicate render basename or invalid image type")
        _require(row.get("sha256") == C.sha256_file(source), "render bytes changed")
        names.add(name)
        sources.append(source)
    return sources


def snapshot(workspace, generation, sheet):
    """Validate every stage and freeze the exact bytes shown to the decider."""
    workspace = Path(workspace)
    directory = C.gen_dir(workspace, generation)
    _require(directory.resolve() == directory, "generation root changed")
    sheet = _file(directory, Path(sheet))
    record_paths = [directory / (name + ".json") for name in ("generate", "inspect", "normalize", "render")]
    records = []
    for path in record_paths:
        _file(directory, path)
        record = C.read_json(path)
        _require(type(record) is dict and record.get("generation") == generation, "receipt generation changed")
        records.append(record)
    generated, inspected, normalized, rendered = records
    try:
        reference = Path(generated["input"]["path"])
        _require(reference.is_absolute() and reference.is_file(), "reference missing")
        reference_sha = C.sha256_file(reference)
        _require(generated["input"]["sha256"] == reference_sha, "reference bytes changed")
        asset = _file(directory / "raw", C.contained_asset(workspace, generation, generated["asset"]["path"]))
        asset_sha = C.sha256_file(asset)
        _require(generated["asset"]["sha256"] == asset_sha, "generated asset changed")
        relative_asset = C.workspace_relative(workspace, asset)
        _require(inspected["facts"]["source"] == relative_asset and inspected["facts"]["sha256"] == asset_sha,
                 "inspection does not describe the generated asset")
        _require(normalized["source"] == relative_asset and normalized["sourceSha256"] == asset_sha,
                 "normalization source changed")
        glb = _file(directory, directory / "normalized.glb")
        glb_sha = C.sha256_file(glb)
        relative_glb = C.workspace_relative(workspace, glb)
        _require(normalized["output"] == relative_glb and normalized["export"]["sha256"] == glb_sha,
                 "normalized GLB changed")
        _require(rendered["glb"] == relative_glb and rendered["glbSha256"] == glb_sha,
                 "renders do not describe the normalized GLB")
        renders = render_sources(workspace, generation, rendered)
        sheet_path = _file(directory, sheet.with_suffix(".json"))
        comparison = C.read_json(sheet_path)
        _require(comparison["generation"] == generation and comparison["output"] == C.workspace_relative(workspace, sheet)
                 and comparison["sha256"] == C.sha256_file(sheet), "comparison sheet changed")
        panels = comparison["panels"]
        _require(type(panels) is list and len(panels) >= 2, "comparison panels missing")
        _require(panels[0]["path"] == C.workspace_relative(workspace, reference)
                 and panels[0]["sha256"] == reference_sha, "comparison reference changed")
        by_path = {C.workspace_relative(workspace, path): path for path in renders}
        for panel in panels[1:]:
            _require(panel["path"] in by_path and panel["sha256"] == C.sha256_file(by_path[panel["path"]]),
                     "comparison render changed")
        raw_entries = list((directory / "raw").rglob("*"))
        _require(not any(path.is_symlink() for path in raw_entries), "raw asset dependency is a symlink")
        C.require_contained_model(workspace, generation, asset)
        raw = [_file(directory / "raw", path) for path in raw_entries if not path.is_dir()]
        files = [*record_paths, *raw, glb, *renders, sheet, sheet_path]
        return {"reference": {"path": str(reference), "sha256": reference_sha},
                "files": {C.workspace_relative(workspace, path): C.sha256_file(path) for path in files}}
    except (KeyError, TypeError, ValueError, OSError) as error:
        raise C.TexturedError("stale or incomplete textured evidence; rerun inspect, normalize, render, sheet and likeness") from error


def model_authority(workspace, run_id, reference, model_id, generation, bindings):
    """Require the same live, journal-proven quality grant at record/decide/delivery."""
    from decision_control import DecisionController, DecisionError, canonical_digest
    from route_no_account import RoutingNoAccount
    reference = Path(reference)
    if not reference.is_absolute():
        reference = Path(workspace) / reference
    _require(str(reference.resolve()) == bindings["reference"]["path"], "model reference differs from generated reference")
    relative = C.workspace_relative(workspace, reference)
    _require(not Path(relative).is_absolute(), "attested reference must be workspace-relative")
    try:
        grant = RoutingNoAccount(workspace, run_id, relative)
        DecisionController(workspace, run_id, routing_authority=grant)
        _require(grant.model_id == model_id and "quality-review" in grant()["purposes"], "model identity or purpose is not authorized")
        return {"runId": run_id, "generation": generation, **grant.metadata(),
                "referenceSha256": bindings["reference"]["sha256"], "bindingsSha256": canonical_digest(bindings)}
    except DecisionError as error:
        raise C.TexturedError("no-account authority refused: " + error.reason_code) from None


def judged(workspace, generation):
    path = _file(C.gen_dir(workspace, generation), C.gen_dir(workspace, generation) / "likeness.json")
    record = C.read_json(path)
    try:
        _require(record["generation"] == generation, "likeness generation changed")
        relative = Path(record["sheet"]["path"])
        _require(not relative.is_absolute() and ".." not in relative.parts, "likeness sheet path changed")
        sheet = _file(C.gen_dir(workspace, generation), Path(workspace) / relative)
        current = snapshot(workspace, generation, sheet)
        _require(record["sheet"]["sha256"] == C.sha256_file(sheet) and record["bindings"] == current,
                 "likeness no longer describes this pipeline")
        if record["provider"] == "host-model":
            authority = record["authority"]
            _require(record["presentedAsJev"] is False and record["decider"].startswith("model:"),
                     "model verdict cannot be presented as Jev")
            expected = model_authority(workspace, authority["runId"], current["reference"]["path"],
                                       record["decider"][6:], generation, current)
            _require(authority == expected, "model authority changed")
        else:
            from jev_backend import MODEL
            _require(record["provider"] == "jev" and record["decider"] == MODEL and record["presentedAsJev"] is True,
                     "unrecognized likeness provider")
        return {"generation": generation, "likenessSha256": C.sha256_file(path), "bindings": current}
    except (KeyError, TypeError, ValueError, OSError) as error:
        raise C.TexturedError("likeness lacks a complete source binding; rerun likeness") from error
