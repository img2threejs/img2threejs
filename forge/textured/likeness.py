#!/usr/bin/env python3
"""Likeness of a render against its reference, as typed questions: one overall choice
(same / similar / different), a 0..4 score per feature derived from the object kind, and
P(defect) checks. Answered by Jev (consented, budgeted) or, with no Jev account, by the host model
under an explicit ``decider: model:<id>`` that is never presented as Jev.

    likeness.py questions --workspace . --gen 1 --kind character          # writes the typed set
    likeness.py run       --workspace . --gen 1 --kind character --run-id R   # Jev, needs live consent
    likeness.py record-model-answers --workspace . --gen 1 --kind character --answers a.json --model-id ID
    likeness.py pick      --workspace . --gens 1,2,3 --reference ref.png --run-id R

Pure stdlib: it composes JSON and reads PNG headers; the sheet itself is made by sheet.py.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import struct
import sys
import tempfile
from pathlib import Path
from typing import Any

# `inspect.py` in this directory shadows the stdlib module: keep the directory at the END of sys.path.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or os.getcwd()) != _HERE]
sys.path.append(_HERE)
import _common as C  # noqa: E402
import jev_backend as J  # noqa: E402
import _chain as chain  # noqa: E402

QUESTION_SET_VERSION = "textured-likeness-v1"
SCORE_LEVELS = (
    "Absent or wrong relative to the reference.",
    "Major mismatch with the reference.",
    "Partially matches the reference, with visible deficiencies.",
    "Matches the reference.",
    "Matches the reference exactly, indistinguishable at this scale.",
)
OVERALL_CHOICES = {
    "same": "Same subject and design; only minor differences.",
    "similar": "Recognisably the same concept but with clear differences in several parts.",
    "different": "A different subject or design.",
}
CONTEXT = ("The single image shows two renders side by side: the LEFT half is the REFERENCE image of a subject; "
           "the RIGHT half is a 3D MODEL render that is meant to reproduce the reference subject. ")
PICK_CONTEXT = ("The single image has panels left to right: PANEL 1 is the REFERENCE image of a subject; the "
                "remaining panels are candidate 3D model renders (A, B, C, D in order) that each try to reproduce it. ")
JEV_MIN_SIDE, JEV_MAX_SIDE = 280, 1568
DEFECT_FLAG = 0.5
ANSWER_DECIDERS = ("jev", "model")

# kind -> list of (id, what to rate). Derived from the vesper feature list, generalised per kind.
FEATURES: dict[str, list[tuple[str, str]]] = {
    "character": [
        ("silhouette_proportions", "silhouette and body proportions (head size, torso, legs, arm length, overall pose)"),
        ("costume_colours_layers", "costume colours, layers, trims and cloth shapes"),
        ("face_head", "the face and head, including any mask, eyes and expression"),
        ("hair", "hair or headwear shape, volume and colour"),
        ("hands", "hands: anatomy, finger count and grip on anything held"),
        ("held_props_accessories", "held props and worn accessories (weapons, bags, belts, jewellery) and how they attach"),
        ("footwear_legwear", "legwear and footwear"),
        ("texture_realism", "surface quality and realism of the materials (cloth, leather, metal, skin) and texture sharpness"),
    ],
    "creature": [
        ("silhouette_proportions", "silhouette, body proportions and pose"),
        ("head_face", "head, eyes, mouth and facial features"),
        ("limbs_extremities", "limbs, claws, hooves, wings or tail"),
        ("skin_fur_scales", "skin, fur, scales or feather pattern, colour and coverage"),
        ("markings_colours", "colours and distinctive markings"),
        ("texture_realism", "surface quality, realism and texture sharpness"),
    ],
    "vehicle": [
        ("silhouette_proportions", "overall silhouette, proportions and stance"),
        ("body_panels_shape", "body panels, window shapes, lines and surfacing"),
        ("wheels_running_gear", "wheels, tyres, tracks, wings or other running gear"),
        ("lights_details", "lights, grilles, handles, intakes and other small details"),
        ("paint_materials", "paint colour, livery, glass and metal finishes"),
        ("texture_realism", "surface quality, realism and texture sharpness"),
    ],
    "prop": [
        ("silhouette_proportions", "overall silhouette and proportions"),
        ("shape_details", "distinct parts, edges and ornamental details"),
        ("materials_colours", "materials and colours (metal, wood, leather, gems, paint)"),
        ("wear_surface", "wear, scratches, engraving and surface detail"),
        ("texture_realism", "surface quality, realism and texture sharpness"),
    ],
    "furniture": [
        ("silhouette_proportions", "overall silhouette and proportions"),
        ("structure_joints", "frame, legs, joints and supports"),
        ("upholstery_materials", "upholstery, wood grain, metal or fabric materials and colours"),
        ("details_trim", "stitching, trim, buttons, hardware and other details"),
        ("texture_realism", "surface quality, realism and texture sharpness"),
    ],
    "building": [
        ("massing_proportions", "overall massing, proportions and roof shape"),
        ("openings_facade", "windows, doors, arches and facade rhythm"),
        ("materials_colours", "wall, roof and trim materials and colours"),
        ("ornament_details", "ornament, signage, railings and other details"),
        ("texture_realism", "surface quality, realism and texture sharpness"),
    ],
    "generic": [
        ("silhouette_proportions", "overall silhouette and proportions"),
        ("shape_details", "distinct parts and the details that identify the subject"),
        ("colours_materials", "colours and materials"),
        ("texture_realism", "surface quality, realism and texture sharpness"),
    ],
}
# kind -> list of (id, noul question). P(yes) is the defect probability.
DEFECTS: dict[str, list[tuple[str, str]]] = {
    "character": [
        ("extra_missing_limbs", "Does the right model have extra or missing limbs, extra heads, or a duplicated body part?"),
        ("melted_geometry", "Does the right model have melted, collapsed or noticeably deformed geometry (blobby, smeared or fused parts)?"),
        ("wrong_hands", "Does the right model have a wrong number of hands or malformed hands (wrong finger count)?"),
        ("floating_parts", "Is any prop or part floating, detached from where it should attach, or passing through the body in the right model?"),
        ("text_logos", "Does the right model show unintended text, letters or logos that are not in the reference?"),
    ],
    "creature": [
        ("extra_missing_limbs", "Does the right model have extra or missing limbs, heads, eyes or tails compared with the reference?"),
        ("melted_geometry", "Does the right model have melted, collapsed or noticeably deformed geometry?"),
        ("floating_parts", "Is any part floating or detached from the body in the right model?"),
        ("text_logos", "Does the right model show unintended text, letters or logos that are not in the reference?"),
    ],
}
_COMMON_DEFECTS = [
    ("missing_duplicated_parts", "Does the right model have a missing or duplicated major part compared with the reference?"),
    ("melted_geometry", "Does the right model have melted, collapsed or noticeably deformed geometry?"),
    ("floating_parts", "Is any part floating, detached or intersecting where it should not in the right model?"),
    ("text_logos", "Does the right model show unintended text, letters or logos that are not in the reference?"),
]


def kinds() -> list[str]:
    return sorted(FEATURES)


def build_questions(kind: str, extra_features: list[tuple[str, str]] | None = None) -> dict[str, dict[str, Any]]:
    """The typed question set. Question ids are namespaced: ``overall``, ``score:<id>``, ``noul:<id>``."""
    if kind not in FEATURES:
        raise C.TexturedError(f"unknown kind {kind!r}; expected one of {kinds()}")
    questions: dict[str, dict[str, Any]] = {
        "overall": {"type": "choice", "criteria": dict(OVERALL_CHOICES), "instructions":
                    "Is the 3D model on the right the same subject as the reference on the left, with the same "
                    "design, pose, colours and proportions?"},
    }
    seen = set()
    for feature_id, what in list(FEATURES[kind]) + list(extra_features or []):
        C.safe_name(feature_id, "feature id")
        if feature_id in seen:
            raise C.TexturedError(f"duplicate feature id {feature_id!r}")
        seen.add(feature_id)
        questions[f"score:{feature_id}"] = {"type": "score", "criteria": list(SCORE_LEVELS), "instructions":
                                            f"Rate how well the right model matches the reference for: {what}."}
    for defect_id, text in DEFECTS.get(kind, _COMMON_DEFECTS):
        questions[f"noul:{defect_id}"] = {"type": "noul", "instructions": text}
    return questions


def parse_extra_features(values: list[str]) -> list[tuple[str, str]]:
    result = []
    for value in values or []:
        feature_id, separator, what = value.partition("=")
        if not separator or not what.strip():
            raise C.TexturedError(f"--feature expects id=what to rate, got {value!r}")
        result.append((feature_id.strip(), what.strip()))
    return result


# ---------------------------------------------------------------- answer validation + summary

def _number(value: Any, low: float, high: float, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or not low <= value <= high:
        raise C.TexturedError(f"{what} must be a number within {low}..{high}")
    return float(value)


def normalize_answers(questions: dict[str, dict[str, Any]], answers: Any) -> dict[str, dict[str, Any]]:
    """Validate answers against the typed set (key set, types, ranges, choice vocabulary).

    Accepted shapes per type: choice ``{"choice": "same", "probabilities": {...}?, "confidence"?}`` or a bare
    choice string; score ``{"score": 2.5}`` or a bare number 0..4; noul ``{"noul": 0.3}`` or a bare number 0..1.
    """
    if not isinstance(answers, dict):
        raise C.TexturedError("answers must be a JSON object keyed by question id")
    if set(answers) != set(questions):
        missing, extra = sorted(set(questions) - set(answers)), sorted(set(answers) - set(questions))
        raise C.TexturedError(f"answers must cover exactly the question set (missing {missing}, unexpected {extra})")
    result: dict[str, dict[str, Any]] = {}
    for key, question in questions.items():
        raw = answers[key]
        kind = question["type"]
        if isinstance(raw, dict) and raw.get("type", kind) != kind:
            raise C.TexturedError(f"answer {key} has type {raw.get('type')!r}, expected {kind!r}")
        out: dict[str, Any] = {"type": kind}
        if kind == "choice":
            choice = raw.get("choice") if isinstance(raw, dict) else raw
            if choice not in question["criteria"]:
                raise C.TexturedError(f"answer {key} must be one of {sorted(question['criteria'])}")
            out["choice"] = choice
            probabilities = raw.get("probabilities") if isinstance(raw, dict) else None
            if probabilities is not None:
                if set(probabilities) != set(question["criteria"]):
                    raise C.TexturedError(f"answer {key} probabilities must cover {sorted(question['criteria'])}")
                out["probabilities"] = {k: _number(v, 0, 1, f"{key} probability") for k, v in probabilities.items()}
                top = max(out["probabilities"], key=out["probabilities"].get)
                out["choice"] = top
            else:
                out["probabilities"] = {k: (1.0 if k == choice else 0.0) for k in question["criteria"]}
        elif kind == "score":
            out["score"] = _number(raw.get("score") if isinstance(raw, dict) else raw, 0, 4, f"{key} score")
        elif kind == "noul":
            out["noul"] = _number(raw.get("noul") if isinstance(raw, dict) else raw, 0, 1, f"{key} noul")
        else:
            raise C.TexturedError(f"unsupported question type {kind!r}")
        if isinstance(raw, dict) and "confidence" in raw:
            out["confidence"] = _number(raw["confidence"], 0, 1, f"{key} confidence")
        result[key] = out
    return result


def summarize(answers: dict[str, dict[str, Any]], defect_flag: float = DEFECT_FLAG) -> dict[str, Any]:
    overall = answers["overall"]
    scores = {k.split(":", 1)[1]: v["score"] for k, v in answers.items() if v["type"] == "score"}
    defects = {k.split(":", 1)[1]: v["noul"] for k, v in answers.items() if v["type"] == "noul"}
    return {
        "overall": {"verdict": overall["choice"], "probabilities": overall["probabilities"]},
        "meanScore": round(sum(scores.values()) / len(scores), 4) if scores else None,
        "scores": scores, "defects": defects,
        "flaggedDefects": sorted(k for k, v in defects.items() if v >= defect_flag),
        "defectFlagThreshold": defect_flag,
    }


# ---------------------------------------------------------------- sheet + payload

def png_size(path: Path) -> tuple[int, int]:
    with open(path, "rb") as stream:
        header = stream.read(24)
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise C.TexturedError(f"not a PNG: {path}")
    return struct.unpack(">II", header[16:24])


def check_sheet(path: Path) -> tuple[int, int]:
    if not path.is_file():
        raise C.TexturedError(f"sheet not found: {path} (run sheet.py first)")
    width, height = png_size(path)
    if not (JEV_MIN_SIDE <= width <= JEV_MAX_SIDE and JEV_MIN_SIDE <= height <= JEV_MAX_SIDE):
        raise C.TexturedError(f"sheet is {width}x{height}; each side must be within {JEV_MIN_SIDE}..{JEV_MAX_SIDE}")
    return width, height


def build_payload(sheet: Path, questions: dict[str, dict[str, Any]], tag: str, context: str = CONTEXT) -> dict[str, Any]:
    import hashlib
    reference = "imgref-" + hashlib.sha256(tag.encode()).hexdigest()[:24]
    prefixed = {key: {**question, "instructions": context + question["instructions"]} for key, question in questions.items()}
    return {
        "model": J.MODEL,
        "state": {"task": "likeness review of a 3D model render against its reference image",
                  "observations": {"images": [{"asset": reference, "role": "left=reference, right=3d-model-render"}]}},
        "questions": prefixed,
        "assets": {reference: {"type": "image", "format": "png",
                               "data_base64": base64.b64encode(sheet.read_bytes()).decode("ascii")}},
    }


def parse_response(questions: dict[str, dict[str, Any]], response: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(response, dict) or response.get("model") != J.MODEL or not isinstance(response.get("answers"), dict):
        raise C.TexturedError("malformed Jev response (model or answers missing)")
    return normalize_answers(questions, response["answers"])


# ---------------------------------------------------------------- journal + receipts

def journal(workspace: Path, event: dict[str, Any]) -> None:
    path = C.textured_root(workspace) / "journal.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as stream:
        stream.write(json.dumps({"at": C.now_iso(), **event}, sort_keys=True) + "\n")


def record_tokens(workspace: Path, row: dict[str, Any]) -> None:
    path = C.textured_root(workspace) / "jev-tokens.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as stream:
        stream.write(json.dumps({"at": C.now_iso(), **row}, sort_keys=True) + "\n")


def tokens_spent(workspace: Path) -> int:
    path = C.textured_root(workspace) / "jev-tokens.jsonl"
    if not path.is_file():
        return 0
    total = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
            total += int(row.get("input_tokens", 0)) + int(row.get("output_tokens", 0))
        except (ValueError, TypeError):
            continue
    return total


def likeness_path(workspace: Path, generation: int) -> Path:
    return C.gen_dir(workspace, generation) / "likeness.json"


def sheet_for(workspace: Path, generation: int, view: str) -> Path:
    return C.gen_dir(workspace, generation) / f"sheet-{C.safe_name(view, '--view')}.png"


def write_likeness(workspace, generation, kind, decider, provider, sheet, questions, answers, **extra):
    path = likeness_path(workspace, generation)
    if os.path.lexists(path):
        raise C.TexturedError("generation already judged (this exact request was already sent); keep its immutable likeness")
    bindings = chain.snapshot(workspace, generation, sheet)
    if extra.pop("bindings", bindings) != bindings:
        raise C.TexturedError("pipeline changed during likeness review; no verdict recorded")
    if provider == "host-model":
        authority = extra.get("authority")
        if type(authority) is not dict or not decider.startswith("model:"):
            raise C.TexturedError("model likeness requires live user authority, never a self-declared outage")
        expected = chain.model_authority(workspace, authority["runId"], bindings["reference"]["path"],
                                         decider[6:], generation, bindings)
        if authority != expected:
            raise C.TexturedError("model likeness authority changed")
    elif provider != "jev" or decider != J.MODEL:
        raise C.TexturedError("unrecognized likeness provider")
    summary = summarize(answers)
    record = C.receipt(
        "likeness", generation=generation, kind=kind, decider=decider, provider=provider,
        presentedAsJev=provider == "jev", questionSetVersion=QUESTION_SET_VERSION,
        sheet={"path": C.workspace_relative(workspace, sheet), "sha256": C.sha256_file(sheet), "size": list(png_size(sheet))},
        questions={k: {"type": v["type"], **({"criteria": v["criteria"]} if "criteria" in v else {}),
                       "instructions": v["instructions"]} for k, v in questions.items()},
        answers=answers, summary=summary, bindings=bindings, **extra)
    fd, temporary = tempfile.mkstemp(prefix=".likeness-", dir=path.parent)
    os.close(fd)
    try:
        C.write_json(Path(temporary), record)
        os.link(temporary, path)
    except FileExistsError:
        raise C.TexturedError("generation already judged; likeness is immutable") from None
    finally:
        Path(temporary).unlink(missing_ok=True)
    journal(workspace, {"event": "likeness", "generation": generation, "decider": decider,
                        "verdict": summary["overall"]["verdict"], "meanScore": summary["meanScore"]})
    return record


# ---------------------------------------------------------------- commands

def cmd_questions(args, workspace):
    questions = build_questions(args.kind, parse_extra_features(args.feature))
    sheet = sheet_for(workspace, args.gen, args.view)
    check_sheet(sheet)
    out = Path(args.out) if args.out else C.gen_dir(workspace, args.gen) / "likeness-questions.json"
    C.write_json(out, C.receipt(
        "likeness-questions", generation=args.gen, kind=args.kind, questionSetVersion=QUESTION_SET_VERSION,
        sheet=C.workspace_relative(workspace, sheet), context=CONTEXT, questions=questions,
        answerFormat={"choice": "one criteria key or {choice, probabilities}", "score": "number 0..4",
                      "noul": "number 0..1 = P(defect present)"},
        decider="model:<your model id> -- record with `likeness.py record-model-answers`; this is NOT Jev"))
    print(f"likeness: {len(questions)} typed questions -> {out}; open {sheet} and answer them")
    return 0


def cmd_run(args, workspace, backend=None):
    questions = build_questions(args.kind, parse_extra_features(args.feature))
    sheet = sheet_for(workspace, args.gen, args.view)
    check_sheet(sheet)
    if os.path.lexists(likeness_path(workspace, args.gen)):
        raise C.TexturedError("generation already judged (this exact request was already sent); keep its immutable likeness")
    bindings = chain.snapshot(workspace, args.gen, sheet)
    backend = backend or J.PluginBackend(Path(args.jev_dir) if args.jev_dir else None)
    payload = build_payload(sheet, questions, tag=f"{workspace.name}-gen{args.gen}-{C.sha256_file(sheet)[:16]}")
    response, usage, grant = J.consult(backend, workspace, args.run_id, payload, timeout=args.timeout)
    record_tokens(workspace, {"generation": args.gen, "runId": args.run_id, "purpose": "likeness",
                              "input_tokens": usage.get("input_tokens", 0), "output_tokens": usage.get("output_tokens", 0)})
    try:
        answers = parse_response(questions, response)
    except C.TexturedError:
        journal(workspace, {"event": "rejected-jev-answer", "generation": args.gen,
                            "runId": args.run_id, "requestSha256": J.request_digest(payload)})
        raise
    record = write_likeness(workspace, args.gen, args.kind, J.MODEL, "jev", sheet, questions, answers,
                            bindings=bindings, usage=usage, consent={"runId": args.run_id, "purpose": J.PURPOSE,
                                                  "tokenBudget": grant["tokenBudget"], "grantSha256": grant.get("sha256")},
                            tokensSpentByLoop=tokens_spent(workspace))
    s = record["summary"]
    print(f"likeness (Jev {J.MODEL}): overall={s['overall']['verdict']} mean={s['meanScore']} "
          f"flagged={s['flaggedDefects'] or 'none'} tokens={usage.get('input_tokens', 0)}+{usage.get('output_tokens', 0)}")
    return 0


def cmd_record_model(args, workspace):
    if not args.model_id.strip() or any(ch.isspace() or ord(ch) < 33 for ch in args.model_id):
        raise C.TexturedError("--model-id must be a non-empty identifier without whitespace")
    questions = build_questions(args.kind, parse_extra_features(args.feature))
    sheet = sheet_for(workspace, args.gen, args.view)
    check_sheet(sheet)
    if os.path.lexists(likeness_path(workspace, args.gen)):
        raise C.TexturedError("generation already judged; model answers cannot replace its verdict")
    bindings = chain.snapshot(workspace, args.gen, sheet)
    authority = chain.model_authority(workspace, args.run_id, args.reference, args.model_id, args.gen, bindings)
    from jev_cli_bridge import _file, _json
    answer_path = Path(args.answers)
    try:
        relative = str(answer_path.relative_to(workspace)) if answer_path.is_absolute() else str(answer_path)
        answers = normalize_answers(questions, _json(_file(workspace, relative, maximum=2 * 1024 * 1024, capture=True)))
    except (ValueError, OSError) as error:
        raise C.TexturedError("invalid or uncontained model answers") from error
    record = write_likeness(workspace, args.gen, args.kind, f"model:{args.model_id}", "host-model", sheet, questions, answers,
                            bindings=bindings, authority=authority,
                            note="answered by the host model, not by Jev; scores are not comparable with Jev scores")
    s = record["summary"]
    print(f"likeness (model:{args.model_id}, NOT Jev): overall={s['overall']['verdict']} mean={s['meanScore']} "
          f"flagged={s['flaggedDefects'] or 'none'}")
    return 0


def cmd_pick(args, workspace, backend=None):
    gens = [int(g) for g in args.gens.split(",") if g.strip()]
    if not 2 <= len(gens) <= 4 or len(set(gens)) != len(gens):
        raise C.TexturedError("--gens needs 2..4 distinct generation numbers")
    sheet = C.textured_root(workspace) / "pick-sheet.png"
    renders = [C.gen_dir(workspace, g) / "renders" / f"{args.view}.png" for g in gens]
    import subprocess
    command = [sys.executable, str(Path(__file__).with_name("sheet.py")), "--workspace", str(workspace),
               "--reference", args.reference, "--out", str(sheet)]
    for path in renders:
        command += ["--panel", str(path)]
    done = subprocess.run(command, capture_output=True, text=True)
    if done.returncode != 0:
        raise C.TexturedError(f"sheet.py failed: {done.stderr.strip() or done.stdout.strip()}")
    check_sheet(sheet)
    letters = "ABCD"[:len(gens)]
    criteria = {letter: f"Candidate {letter} (panel {i + 2}) is the closest reproduction of the reference."
                for i, letter in enumerate(letters)}
    questions = {"closest_overall": {"type": "choice", "criteria": criteria, "instructions":
                 "Which candidate is the closest reproduction of the reference overall (design, proportions, colours, details, pose)?"}}
    backend = backend or J.PluginBackend(Path(args.jev_dir) if args.jev_dir else None)
    payload = build_payload(sheet, questions, tag=f"{workspace.name}-pick-{C.sha256_file(sheet)[:16]}", context=PICK_CONTEXT)
    response, usage, _ = J.consult(backend, workspace, args.run_id, payload, timeout=args.timeout)
    record_tokens(workspace, {"generation": 0, "runId": args.run_id, "purpose": "pick",
                              "input_tokens": usage.get("input_tokens", 0), "output_tokens": usage.get("output_tokens", 0)})
    try:
        answers = parse_response(questions, response)
    except C.TexturedError:
        journal(workspace, {"event": "rejected-jev-pick", "gens": gens, "runId": args.run_id,
                            "requestSha256": J.request_digest(payload)})
        raise
    winner = gens[letters.index(answers["closest_overall"]["choice"])]
    out = C.textured_root(workspace) / "pick.json"
    C.write_json(out, C.receipt("pick", gens=gens, panelMap=dict(zip(letters, gens)), decider=J.MODEL, provider="jev",
                                answer=answers["closest_overall"], winner=winner, usage=usage))
    journal(workspace, {"event": "pick", "gens": gens, "winner": winner, "decider": J.MODEL})
    print(f"pick (Jev): generation {winner} -> {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Typed likeness questions for a textured model render")
    commands = parser.add_subparsers(dest="command", required=True)

    def common(sub, gen=True):
        sub.add_argument("--workspace", default=".")
        if gen:
            sub.add_argument("--gen", type=int, required=True)
            sub.add_argument("--kind", choices=kinds(), default="generic")
            sub.add_argument("--view", default="front")
            sub.add_argument("--feature", action="append", default=[], help="extra scored feature: id=what to rate")
    questions = commands.add_parser("questions", help="write the typed question set for the host model (no-account mode)")
    common(questions)
    questions.add_argument("--out")
    run = commands.add_parser("run", help="ask Jev (needs a live per-run decision consent)")
    common(run)
    run.add_argument("--run-id", required=True)
    run.add_argument("--jev-dir", help="Jev plugin tools dir (default $IMG2_HOME/plugins/jev/tools)")
    run.add_argument("--timeout", type=float, default=240.0)
    record = commands.add_parser("record-model-answers", help="record host-model answers as decider model:<id>")
    common(record)
    record.add_argument("--answers", required=True, help="Workspace-relative JSON answers (or contained absolute path)")
    record.add_argument("--model-id", required=True)
    record.add_argument("--run-id", required=True, help="Live user-attested v2 run")
    record.add_argument("--reference", required=True, help="Workspace-relative attested original reference (or contained absolute path)")
    pick = commands.add_parser("pick", help="head-to-head pick between generations through Jev")
    common(pick, gen=False)
    pick.add_argument("--gens", required=True, help="comma separated generation numbers (2..4)")
    pick.add_argument("--reference", required=True)
    pick.add_argument("--view", default="front")
    pick.add_argument("--run-id", required=True)
    pick.add_argument("--jev-dir")
    pick.add_argument("--timeout", type=float, default=240.0)
    return parser


def main(argv, backend=None):
    args = build_parser().parse_args(argv)
    workspace = C.resolve_workspace(args.workspace)
    if args.command == "questions":
        return cmd_questions(args, workspace)
    if args.command == "run":
        return cmd_run(args, workspace, backend)
    if args.command == "record-model-answers":
        return cmd_record_model(args, workspace)
    return cmd_pick(args, workspace, backend)


if __name__ == "__main__":
    C.run_cli(main)
