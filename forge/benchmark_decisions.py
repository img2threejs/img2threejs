"""Offline benchmark of independently frozen expert labels and measured reports.

CLI: python3 forge/benchmark_decisions.py --manifest M --results R --out O
No provider calls, rendering, image dependencies, or policy updates occur here.

JSON v1 protocol (paths are contained relative to the declaring JSON file):
* Manifest: kind=img2.decision-benchmark-manifest, version=1, corpusKind
  (real or protocol-fixture), authorities={id:{kind:expert|source,
  status:verified,attestation:{path,sha256}}}, labels={path,sha256},
  frozen={beforeHeldOut:true,rubric:{path,sha256},repairCriteria:{path,sha256}},
  budgets={tokenBudget,correctionCeiling}, cases=[{id,objectId,group,class,
  split:development|held-out,source:{path,sha256,authority},baseline?:binding}].
* Labels file: labels=[{caseId,authority,decision,boxes?:[{label,box}],
  criticalNegative?:bool}]. Labels are never derived from a result.
* Rubric file: cases={caseId:{features:[{id,tier,threshold}]}} for review/repair
  and any optional measured candidate comparisons.
* Repair criteria file: cases={caseId:criteria}; criteria follow decision_quality.
* Results: kind=img2.decision-benchmark-results,version=1,results=[{caseId,
  sourceSha256,measurement:{path,sha256}}]. Each measurement is independent
  captured JSON with producer, decision, elapsedMs, usage={fresh,cached,output},
  and decider=jev:<model>|model:<model> (required for real corpus evaluation).
  audit={mutations:[{id,forbidden,admitted}],corrections}, and bindings=[binding].
  Bindings cover source/artifact evidence; quality candidates' reference/camera/
  view-set hashes must all occur in that set. Review adds candidate and delivered;
  repair adds before,after,retained,delivered. Routing adds boxes. Other groups
  may include candidates for deterministic quality ranking. A case's baseline
  binding contains a candidate report, required for positive review cases.
  Real-corpus primary-provider qualification uses Jev-attributed rows, not
  model-fallback accuracy; both remain visible in per-decider summaries.
  Result rows never supply expert labels,
  baselines, frozen criteria, or rubrics.

Authority attestations and freeze declarations are caller-supplied provenance,
not independently authenticated expert credentials. Protocol fixtures NEVER
qualify real-image quality, regardless of their measured protocol accuracy.
Missing/invalid evidence is named unevaluated; unknown counters remain null.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

from _shared.decision_quality import assess_candidate, compare_repair, rank_candidates

GROUPS = ("routing", "build-strategy", "quality-review", "repair")
CLASSES = (
    "generic-object", "room-with-props", "character-rig", "character-no-rig",
    "cs2-real", "cs2-lookalike", "flat-graphic", "ambiguous-scene",
    "multi-object-scene", "adversarial-route-card",
)
SPLITS = ("development", "held-out")
TOKEN_FIELDS = ("fresh", "cached", "output")
BOX_IOU = 0.5


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def _integer(value):
    return type(value) is int and value >= 0


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _hash(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _reject_constant(value):
    raise ValueError("non-finite JSON number: " + value)


def _finite_float(value):
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("non-finite JSON number")
    return parsed


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key: " + key)
        result[key] = value
    return result


def _load(path):
    with Path(path).open(encoding="utf-8") as stream:
        return json.load(stream, parse_constant=_reject_constant, parse_float=_finite_float, object_pairs_hook=_unique_pairs)


def _binding(binding, root, name, omissions, *, json_file=False):
    if not isinstance(binding, dict) or not _text(binding.get("path")) or not _hash(binding.get("sha256")):
        omissions.append(name + ":missing-path-or-sha256")
        return None
    base = Path(root).resolve()
    relative = Path(binding["path"])
    path = base / relative
    try:
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("path-escape")
        cursor = base
        for part in relative.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise ValueError("symlink")
        path.resolve().relative_to(base)
        if hashlib.sha256(path.read_bytes()).hexdigest() != binding["sha256"]:
            raise ValueError("hash-mismatch")
        return _load(path) if json_file else binding["sha256"]
    except (OSError, ValueError, TypeError) as error:
        # Report a bounded reason, not untrusted file contents or machine paths.
        reason = str(error) if str(error) in ("path-escape", "symlink", "hash-mismatch") else "unreadable-or-invalid"
        omissions.append(name + ":" + reason)
        return None


def _index(rows, field, name, omissions):
    indexed = {}
    duplicates = set()
    if not isinstance(rows, list):
        omissions.append(name + ":missing-list")
        return indexed
    for position, row in enumerate(rows):
        key = row.get(field) if isinstance(row, dict) else None
        if not _text(key):
            omissions.append(name + ":invalid-id:" + str(position))
        elif key in indexed or key in duplicates:
            omissions.append(name + ":duplicate:" + key)
            indexed.pop(key, None)
            duplicates.add(key)
        else:
            indexed[key] = row
    return indexed


def _box(item):
    if not isinstance(item, dict) or not _text(item.get("label")):
        return None
    box = item.get("box")
    if (not isinstance(box, list) or len(box) != 4
            or not all(_number(v) and v <= 1 for v in box)
            or box[0] >= box[2] or box[1] >= box[3]):
        return None
    return box


def _iou(first, second):
    intersection = max(0, min(first[2], second[2]) - max(first[0], second[0])) * max(
        0, min(first[3], second[3]) - max(first[1], second[1]))
    union = (first[2] - first[0]) * (first[3] - first[1]) + (
        second[2] - second[0]) * (second[3] - second[1]) - intersection
    return intersection / union


def _box_counts(expected, actual, omissions):
    if (not isinstance(expected, list) or not expected or not isinstance(actual, list)
            or any(_box(item) is None for item in expected + actual)):
        omissions.append("boxes:missing-or-invalid")
        return None
    # Maximum bipartite matching: one predicted box cannot recall two objects.
    edges = [[index for index, predicted in enumerate(actual)
              if predicted["label"] == truth["label"] and _iou(truth["box"], predicted["box"]) >= BOX_IOU]
             for truth in expected]
    assigned = {}

    def match(truth, seen):
        for prediction in edges[truth]:
            if prediction in seen:
                continue
            seen.add(prediction)
            if prediction not in assigned or match(assigned[prediction], seen):
                assigned[prediction] = truth
                return True
        return False

    matched = sum(match(index, set()) for index in range(len(expected)))
    return {"matched": matched, "expected": len(expected), "predicted": len(actual)}


def _rubric(candidate, rubric, name, omissions):
    features = rubric.get("features") if isinstance(rubric, dict) else None
    actual = candidate.get("features") if isinstance(candidate, dict) else None
    if not isinstance(features, list) or not isinstance(actual, list):
        omissions.append(name + ":missing-frozen-rubric")
        return
    fields = ("id", "tier", "threshold")
    if (any(not isinstance(item, dict) or not _text(item.get("id"))
            or item.get("tier") not in ("critical", "important")
            or not _number(item.get("threshold")) or item["threshold"] > 4 for item in features)
            or len({item["id"] for item in features}) != len(features)):
        omissions.append(name + ":invalid-frozen-rubric")
        return
    wanted = {item["id"]: tuple(item.get(key) for key in fields) for item in features}
    observed = {item.get("id"): tuple(item.get(key) for key in fields)
                for item in actual if isinstance(item, dict) and _text(item.get("id"))}
    if wanted != observed or len(actual) != len(observed):
        omissions.append(name + ":rubric-mismatch")


def _candidate(candidate, hashes, source, rubric, name, omissions):
    _rubric(candidate, rubric, name, omissions)
    assessment = assess_candidate(candidate)
    for check in assessment["checks"]:
        if check["status"] == "unevaluated":
            omissions.append(name + ":" + check["reason"])
    evidence = candidate.get("evidence", {}) if isinstance(candidate, dict) else {}
    evidence = evidence if isinstance(evidence, dict) else {}
    if evidence.get("referenceSha256") != source:
        omissions.append(name + ":reference-source-mismatch")
    for field in ("referenceSha256", "cameraSha256", "viewSetSha256"):
        if not _hash(evidence.get(field)) or evidence[field] not in hashes:
            omissions.append(name + ":unbound:" + field)
    return assessment


def _total(values):
    return sum(values) if values and all(value is not None for value in values) else None


def _aggregate(rows):
    evaluated = [row for row in rows if row["status"] != "unevaluated"]
    correct = sum(row["correct"] is True for row in evaluated)
    boxes = [row["boxes"] for row in rows if row["boxes"] is not None]
    expected = sum(item["expected"] for item in boxes)
    matched = sum(item["matched"] for item in boxes)
    return {
        "cases": len(rows), "evaluated": len(evaluated), "unevaluated": len(rows) - len(evaluated),
        "correct": correct, "disagreements": sum(row["correct"] is False for row in evaluated),
        "accuracy": correct / len(evaluated) if evaluated else None,
        "validated": sum(row["validated"] for row in evaluated),
        "baselineDenominator": sum(row["baselineEvaluated"] for row in rows),
        "boxMatched": matched, "boxDenominator": expected,
        "boxRecall": matched / expected if expected else None,
        "criticalNegativeDenominator": sum(row["criticalNegative"] is True for row in rows),
        "criticalFalseContinue": sum(row["criticalFalseContinue"] for row in rows),
        "forbiddenMutationCount": _total([row["forbiddenMutationCount"] for row in rows]),
        "regressionCount": _total([row["regressionCount"] for row in rows]),
        "acceptedRegressionCount": _total([row["acceptedRegressionCount"] for row in rows]),
        "safetyViolationCount": sum(row["safetyViolationCount"] for row in rows),
        "elapsedMs": _total([row["elapsedMs"] for row in rows]),
        "tokens": {key: _total([row["tokens"][key] for row in rows]) for key in TOKEN_FIELDS},
    }


def evaluate(manifest_path, results_path):
    """Read local evidence and return a JSON-compatible, fail-closed report."""
    manifest_path, results_path = Path(manifest_path), Path(results_path)
    omissions = []
    try:
        manifest, results = _load(manifest_path), _load(results_path)
    except (OSError, ValueError, TypeError):
        return {"kind": "img2.decision-benchmark", "version": 1, "cases": [],
                "unevaluated": ["manifest-or-results:unreadable-or-invalid"],
                "qualification": {"qualified": False, "decisionOnly": False, "execution": False}}
    if not isinstance(manifest, dict) or not isinstance(results, dict):
        return {"kind": "img2.decision-benchmark", "version": 1, "cases": [],
                "unevaluated": ["manifest-or-results:expected-object"],
                "qualification": {"qualified": False, "decisionOnly": False, "execution": False}}
    for value, expected, name in ((manifest, "img2.decision-benchmark-manifest", "manifest"),
                                  (results, "img2.decision-benchmark-results", "results")):
        if value.get("kind") != expected or type(value.get("version")) is not int or value["version"] != 1:
            omissions.append(name + ":unsupported-kind-or-version")
    real = manifest.get("corpusKind") == "real"
    if not real:
        omissions.append("corpus:not-real-image-corpus")
    authorities = manifest.get("authorities", {})
    authorities = authorities if isinstance(authorities, dict) else {}
    verified = {"expert": set(), "source": set()}
    for identity, authority in authorities.items():
        if (not isinstance(authority, dict) or not _text(authority.get("kind")) or authority["kind"] not in verified
                or authority.get("status") != "verified"):
            omissions.append("authority:" + identity + ":unknown-or-unverified")
        elif _binding(authority.get("attestation"), manifest_path.parent,
                      "authority:" + identity, omissions):
            verified[authority["kind"]].add(identity)
    labels_doc = _binding(manifest.get("labels"), manifest_path.parent, "labels", omissions, json_file=True)
    labels = _index(labels_doc.get("labels") if isinstance(labels_doc, dict) else None,
                    "caseId", "labels", omissions)
    frozen = manifest.get("frozen", {})
    frozen = frozen if isinstance(frozen, dict) else {}
    if frozen.get("beforeHeldOut") is not True:
        omissions.append("freeze:not-declared-before-held-out")
    frozen_docs = {}
    for name in ("rubric", "repairCriteria"):
        doc = _binding(frozen.get(name), manifest_path.parent, "freeze:" + name, omissions, json_file=True)
        frozen_docs[name] = doc.get("cases", {}) if isinstance(doc, dict) else {}
        if not isinstance(frozen_docs[name], dict):
            omissions.append("freeze:" + name + ":invalid-cases")
            frozen_docs[name] = {}
    cases = _index(manifest.get("cases"), "id", "cases", omissions)
    measured = _index(results.get("results"), "caseId", "results", omissions)
    for name, mapping in (("labels", labels), ("results", measured)):
        for identity in mapping.keys() - cases.keys():
            omissions.append(name + ":unexpected-case:" + identity)
    # Identical source bytes may not occur on both sides of the held-out boundary.
    split_hashes = {split: set() for split in SPLITS}
    for case in cases.values():
        if case.get("split") in SPLITS and isinstance(case.get("source"), dict):
            digest = case["source"].get("sha256")
            if _hash(digest):
                split_hashes[case["split"]].add(digest)
    leaked = split_hashes["development"] & split_hashes["held-out"]
    for digest in sorted(leaked):
        omissions.append("split:source-leakage:" + digest)
    rows = []
    for identity, case in cases.items():
        missing = []
        group, category, split = case.get("group"), case.get("class"), case.get("split")
        for field, valid in (("group", group in GROUPS), ("class", category in CLASSES),
                             ("split", split in SPLITS), ("objectId", _text(case.get("objectId")))):
            if not valid:
                missing.append(field + ":missing-or-invalid")
        source = _binding(case.get("source"), manifest_path.parent, "source", missing)
        source_authority = case.get("source", {}).get("authority") if isinstance(case.get("source"), dict) else None
        source_valid = isinstance(source_authority, str) and source_authority in verified["source"]
        if not source_valid:
            missing.append("source:unknown-authority")
        label = labels.get(identity)
        label = label if isinstance(label, dict) else {}
        expert_valid = isinstance(label.get("authority"), str) and label["authority"] in verified["expert"]
        if not expert_valid:
            missing.append("label:missing-or-unknown-expert-authority")
        expected = label.get("decision")
        if not _text(expected):
            missing.append("label:missing-decision")
        entry = measured.get(identity)
        if not isinstance(entry, dict):
            missing.append("result:missing")
            entry = {}
        if not source or entry.get("sourceSha256") != source:
            missing.append("result:source-hash-mismatch-or-missing")
        measurement = _binding(entry.get("measurement"), results_path.parent, "measurement", missing, json_file=True)
        report = measurement if isinstance(measurement, dict) else {}
        decider = report.get("decider")
        if not _text(decider):
            decider = None
        if real and (decider is None or not decider.startswith(("jev:", "model:"))
                     or not decider.partition(":")[2]):
            missing.append("measurement:missing-or-invalid-decider")
        if not isinstance(measurement, dict):
            missing.append("measurement:missing-object")
        if not _text(report.get("producer")):
            missing.append("measurement:missing-producer")
        decision = report.get("decision")
        if not _text(decision):
            missing.append("result:missing-decision")
        hashes = {source} if source else set()
        bindings = report.get("bindings")
        if not isinstance(bindings, list) or not bindings:
            missing.append("measurement:missing-evidence-bindings")
        else:
            # A measurement file's bindings are relative to that file, not the index.
            measurement_root = results_path.parent / Path(entry["measurement"]["path"]).parent
            for index, binding in enumerate(bindings):
                digest = _binding(binding, measurement_root, "evidence:" + str(index), missing)
                if digest:
                    hashes.add(digest)
        elapsed = report.get("elapsedMs")
        if not _number(elapsed):
            missing.append("elapsedMs:missing-or-invalid")
            elapsed = None
        usage = report.get("usage", {})
        usage = usage if isinstance(usage, dict) else {}
        tokens = {}
        for key in TOKEN_FIELDS:
            tokens[key] = usage.get(key) if _integer(usage.get(key)) else None
            if tokens[key] is None:
                missing.append("tokens:" + key + ":missing-or-invalid")
        audit = report.get("audit", {})
        audit = audit if isinstance(audit, dict) else {}
        mutations, corrections = audit.get("mutations"), audit.get("corrections")
        forbidden = None
        if (not isinstance(mutations, list) or any(not isinstance(item, dict)
                or not _text(item.get("id")) or type(item.get("forbidden")) is not bool
                or type(item.get("admitted")) is not bool for item in mutations)):
            missing.append("audit:missing-or-invalid-mutations")
        else:
            forbidden = sum(item["forbidden"] and item["admitted"] for item in mutations)
        if not _integer(corrections):
            missing.append("audit:missing-or-invalid-corrections")
            corrections = None
        critical_negative = label.get("criticalNegative") if group == "quality-review" else None
        false_continue = int(critical_negative is True and decision == "continue")
        safety = 0
        regressions = accepted_regressions = 0
        quality = None
        boxes = None
        baseline_evaluated = False
        rubric = frozen_docs["rubric"].get(identity)
        if group == "routing":
            boxes = _box_counts(label.get("boxes"), report.get("boxes"), missing)
        elif group == "quality-review":
            if type(critical_negative) is not bool:
                missing.append("label:missing-critical-negative-flag")
            if expected not in ("continue", "block") or decision not in ("continue", "block"):
                missing.append("quality:invalid-decision")
            quality = _candidate(report.get("candidate"), hashes, source, rubric, "candidate", missing)
            frozen_features = rubric.get("features") if isinstance(rubric, dict) else None
            if critical_negative is True and (not isinstance(frozen_features, list) or not any(
                    isinstance(feature, dict) and feature.get("tier") == "critical"
                    for feature in frozen_features)):
                missing.append("label:critical-negative-without-critical-rubric")
            # Negative review fixtures test rejection, not generated-output fidelity.
            # Execution baselines need separate positive cases in every class.
            if expected == "continue":
                regressions = None
                baseline = _binding(case.get("baseline"), manifest_path.parent, "baseline", missing, json_file=True)
                baseline_assessment = _candidate(baseline, hashes, source, rubric, "baseline", missing)
                rankings = rank_candidates([baseline, report.get("candidate")])
                comparable = not any(item.get("status") == "unevaluated" for item in rankings)
                if not comparable:
                    missing.append("baseline:incomparable")
                if not baseline_assessment["deliverable"]:
                    missing.append("baseline:not-deliverable")
                baseline_evaluated = comparable and baseline_assessment["deliverable"] and not missing
                if baseline_assessment["deliverable"] and quality["deliverable"]:
                    current = report["candidate"]["metrics"]
                    previous = baseline["metrics"]
                    regressions = int(any(current[key] < previous[key] for key in ("silhouetteIoU", "orbitConsistency"))
                                      or current["interiorDifference"] > previous["interiorDifference"])
                elif baseline_assessment["deliverable"] and quality["status"] == "fail":
                    regressions = 1
            accepted_regressions = regressions if decision == "continue" else 0
            if type(report.get("delivered")) is not bool:
                missing.append("quality:missing-delivery-outcome")
            safety += int((decision == "continue" or report.get("delivered") is True) and not quality["deliverable"])
        elif group == "repair":
            before, after = report.get("before"), report.get("after")
            _candidate(before, hashes, source, rubric, "before", missing)
            _candidate(after, hashes, source, rubric, "after", missing)
            criteria = frozen_docs["repairCriteria"].get(identity)
            quality = compare_repair(before, after, criteria)
            if quality["status"] == "unevaluated":
                missing.extend("repair:" + reason for reason in quality["reasons"])
            regressions = (None if quality["status"] == "unevaluated" else
                           int(any(reason.startswith("regression:") for reason in quality["reasons"])))
            for key in ("retained", "delivered"):
                if type(report.get(key)) is not bool:
                    missing.append("repair:missing-" + key + "-outcome")
            accepted_regressions = regressions if report.get("retained") is True else 0
            safety += int(report.get("retained") is True and quality["status"] != "retain")
            safety += int(report.get("delivered") is True and not quality["deliverable"])
        if group in ("routing", "build-strategy") and "candidates" in report:
            candidates = report["candidates"]
            if not isinstance(candidates, list) or not candidates:
                missing.append("candidates:missing-list")
            else:
                for index, candidate in enumerate(candidates):
                    _candidate(candidate, hashes, source, rubric, "candidate:" + str(index), missing)
                quality = rank_candidates(candidates)
                chosen = next((candidate for candidate in quality if candidate["id"] == decision), None)
                if chosen is None or chosen.get("rank") is None:
                    safety += 1
                    missing.append("candidates:selected-candidate-not-comparable-or-deliverable")
        if source in leaked:
            missing.append("split:source-leakage")
        errors = []
        if decision != expected and _text(decision) and _text(expected):
            errors.append("expert-disagreement")
        if false_continue:
            errors.append("critical-false-continue")
        if forbidden:
            errors.append("forbidden-mutation-admitted")
        if safety:
            errors.append("deterministic-quality-or-retention-violation")
        status = "unevaluated" if missing else "fail" if errors else "pass"
        row = {"id": identity, "objectId": case.get("objectId"), "group": group, "class": category,
               "split": split, "status": status, "expectedDecision": expected, "decision": decision,
               "decider": decider,
               "correct": decision == expected if not missing else None,
               "validated": not missing and real and expert_valid and source_valid,
               "boxes": boxes, "criticalNegative": critical_negative, "criticalFalseContinue": false_continue,
               "forbiddenMutationCount": forbidden, "regressionCount": regressions if measurement else None,
               "acceptedRegressionCount": accepted_regressions if measurement else None,
               "safetyViolationCount": safety, "quality": quality,
               "baselineEvaluated": baseline_evaluated,
               "elapsedMs": elapsed, "tokens": tokens, "corrections": corrections,
               "unevaluated": sorted(set(missing)), "errors": errors}
        rows.append(row)
        omissions.extend("case:" + identity + ":" + reason for reason in row["unevaluated"])
    summaries = {}
    for split in SPLITS:
        summaries[split] = {}
        for group in GROUPS:
            selected = [row for row in rows if row["split"] == split and row["group"] == group]
            summaries[split][group] = {"overall": _aggregate(selected), "classes": {}}
            summaries[split][group]["byDecider"] = {
                decider: _aggregate([row for row in selected if (row["decider"] or "unattributed") == decider])
                for decider in sorted({row["decider"] or "unattributed" for row in selected})}
            primary = [row for row in selected if row["decider"] and row["decider"].startswith("jev:")]
            summaries[split][group]["jev"] = _aggregate(primary)
            for category in CLASSES:
                subset = [row for row in selected if row["class"] == category]
                summaries[split][group]["classes"][category] = _aggregate(subset)
                if split == "held-out" and not subset:
                    omissions.append("coverage:held-out:" + group + ":" + category)
                if real and split == "held-out" and not any(
                        row["decider"] and row["decider"].startswith("jev:") for row in subset):
                    omissions.append("coverage:held-out:jev:" + group + ":" + category)
    budgets = manifest.get("budgets", {})
    budgets = budgets if isinstance(budgets, dict) else {}
    for field in ("tokenBudget", "correctionCeiling"):
        if not _integer(budgets.get(field)):
            omissions.append("budget:missing-or-invalid:" + field)
    objects = {}
    for identity in sorted({row["objectId"] for row in rows if _text(row["objectId"])}):
        subset = [row for row in rows if row["objectId"] == identity]
        elapsed = _total([row["elapsedMs"] for row in subset])
        corrections = _total([row["corrections"] for row in subset])
        tokens = {key: _total([row["tokens"][key] for row in subset]) for key in TOKEN_FIELDS}
        objects[identity] = {"elapsedMs": elapsed, "wallClockLimitMs": 3600000,
                             "tokens": tokens, "corrections": corrections}
        if elapsed is None or corrections is None:
            omissions.append("budget:object:" + identity + ":unmeasured")
        if elapsed is not None and elapsed > 3600000:
            omissions.append("budget:object:" + identity + ":wall-clock-exceeded")
        if corrections is not None and _integer(budgets.get("correctionCeiling")) and corrections > budgets["correctionCeiling"]:
            omissions.append("budget:object:" + identity + ":correction-ceiling-exceeded")
    totals = {key: _total([row["tokens"][key] for row in rows]) for key in TOKEN_FIELDS}
    consumed = _total(list(totals.values()))
    if consumed is None:
        omissions.append("budget:run:unmeasured-tokens")
    elif _integer(budgets.get("tokenBudget")) and consumed > budgets["tokenBudget"]:
        omissions.append("budget:run:token-budget-exceeded")
    held = summaries["held-out"]
    route, strategy, review, repair = (held[group]["overall"] for group in GROUPS)
    primary_route = held["routing"]["jev"] if real else route
    primary_strategy = held["build-strategy"]["jev"] if real else strategy
    primary_repair = held["repair"]["jev"] if real else repair
    criteria_met = {
        "routeAccuracy": primary_route["accuracy"] is not None and primary_route["accuracy"] >= 0.85,
        "boxRecall": route["boxRecall"] is not None and route["boxRecall"] >= 0.80,
        "strategyAgreement": primary_strategy["accuracy"] is not None and primary_strategy["accuracy"] >= 0.85,
        "repairAgreement": primary_repair["accuracy"] is not None and primary_repair["accuracy"] >= 0.85,
        "criticalFalseContinue": review["criticalNegativeDenominator"] > 0 and review["criticalFalseContinue"] == 0,
        "forbiddenMutations": all(held[group]["overall"]["forbiddenMutationCount"] == 0 for group in GROUPS),
        "protectedRegressions": all(held[group]["overall"]["acceptedRegressionCount"] == 0 for group in GROUPS),
        "perClassQualityBaseline": all(held["quality-review"]["classes"][category]["baselineDenominator"] > 0
                                       and held["quality-review"]["classes"][category]["regressionCount"] == 0
                                       for category in CLASSES),
        "deterministicSafety": all(held[group]["overall"]["safetyViolationCount"] == 0 for group in GROUPS),
    }
    complete = not omissions and bool(rows)
    decision_only = complete and criteria_met["routeAccuracy"] and criteria_met["boxRecall"]
    execution = complete and all(criteria_met.values())
    return {"kind": "img2.decision-benchmark", "version": 1,
            "corpusKind": manifest.get("corpusKind"),
            "authorityBasis": "Caller-supplied hash-bound attestations; not independently authenticated credentials.",
            "boxIoUThreshold": BOX_IOU, "cases": rows, "splits": summaries,
            "objects": objects, "tokens": totals,
            "knownTokenSubtotals": {key: sum(row["tokens"][key] for row in rows if row["tokens"][key] is not None)
                                    for key in TOKEN_FIELDS},
            "unevaluated": sorted(set(omissions)),
            "qualification": {"qualified": execution, "decisionOnly": decision_only, "execution": execution,
                              "criteriaMet": criteria_met,
                              "failedCriteria": [key for key, value in criteria_met.items() if not value]}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    # Never overwrite the independently supplied evidence being evaluated.
    if args.out.resolve() in (args.manifest.resolve(), args.results.resolve()):
        parser.error("--out must differ from --manifest and --results")
    report = evaluate(args.manifest, args.results)
    try:
        with args.out.open("w", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
    except OSError as error:
        parser.exit(2, "Cannot write benchmark report: " + type(error).__name__ + "\n")
    # Qualification is data, not a CLI invocation failure; protocol/partial reports
    # are useful outputs. Malformed inputs also produce a fail-closed report.
    return 0


if __name__ == "__main__":
    sys.exit(main())
