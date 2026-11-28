"""Revision-bound visual evidence adapter; only the base decides continuation.

API: ``review_quality(controller, context, **consult_callbacks)`` accepts the
shared quality-review context. It never captures, measures, imports a plugin, or
claims that file integrity proves a real screenshot or a trusted producer.
The caller must independently admit the producers and capture/readback evidence.

Required unique binding roles are reference, render, camera, spec, source,
quality-contract, view-set, gate-report and quality-report. JSON contracts:

* quality-contract is the frozen context ``requirements`` object, including
  rubricVersion='quality-review-rubric-v1', dimensions and criticalFeatures.
* view-set is {subject, inputs, views:[{id, role, sha256}], features:[{id,
  referenceRole, referenceSha256, renderRole, renderSha256}]}. Its inputs bind
  reference/render/camera/spec/source/quality-contract. Each view/crop role has
  its own file binding; feature crops belong to this manifest's matched pair.
* gate-report is {subject, inputs, visionAllowed:bool, hardGateFailures:[],
  gateResults:[]}; inputs additionally bind view-set. It is the independently
  produced deterministic admission, not a provider's declaration.
* quality-report is {subject, inputs, candidate}; inputs additionally bind
  gate-report. candidate is the measured decision_quality.assess_candidate shape.

Input maps are role -> SHA256, without local paths. Independent feature scores
and statuses remain separate from Jev/model Choices (which have no feature
Score in the shared schema). Both must pass. No missing value is synthesized.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import PurePosixPath
import stat

from _shared.decision_control import DecisionError, canonical_digest
from _shared.decision_quality import assess_candidate


_PAIR_ROLES = ("reference", "render", "camera", "spec", "source", "quality-contract")
_JSON_ROLES = {"quality-contract", "view-set", "gate-report", "quality-report"}
_REQUIRED_ROLES = set(_PAIR_ROLES) | _JSON_ROLES
_DIMENSIONS = {"silhouette-proportions", "part-relationships", "geometry",
               "material-appearance", "identity-features"}


def _require(condition, reason):
    if not condition:
        raise DecisionError(reason)


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _read_bindings(controller, context):
    """Hash before admission, with the controller's no-symlink path policy."""
    bindings = context.get("bindings")
    _require(type(bindings) is list and bindings, "evidence-missing")
    hashes, documents = {}, {}
    for binding in bindings:
        _require(type(binding) is dict and set(binding) == {"role", "path", "sha256"}, "invalid-binding")
        role, relative = binding["role"], binding["path"]
        _require(type(role) is str and role not in hashes, "invalid-binding")
        _require(type(relative) is str and relative and "\\" not in relative
                 and not PurePosixPath(relative).is_absolute()
                 and all(part not in ("", ".", "..") for part in relative.split("/")), "unsafe-path")
        path = controller.workspace / relative
        _require(not any(path == root or root in path.parents
                         for root in (controller.control_dir, controller.record_root)), "unsafe-path")
        directory = os.open(controller.workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            parts = relative.split("/")
            for component in parts[:-1]:
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory)
                directory = child
            descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            chunks, digest, size = [], hashlib.sha256(), 0
            with os.fdopen(descriptor, "rb") as stream:
                _require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "unsafe-path")
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
                    if role in _JSON_ROLES:
                        size += len(chunk)
                        _require(size <= 1024 * 1024, "evidence-too-large")
                        chunks.append(chunk)
        except OSError as exc:
            raise DecisionError("evidence-missing" if isinstance(exc, FileNotFoundError) else "unsafe-path") from None
        finally:
            os.close(directory)
        _require(digest.hexdigest() == binding["sha256"], "stale-evidence")
        hashes[role] = digest.hexdigest()
        if role in _JSON_ROLES:
            try:
                document = json.loads(b"".join(chunks))
            except (ValueError, UnicodeError):
                raise DecisionError("invalid-evidence") from None
            _require(type(document) is dict, "invalid-evidence")
            canonical_digest(document)
            documents[role] = document
    _require(_REQUIRED_ROLES <= hashes.keys(), "evidence-missing")
    return hashes, documents


def _bound(document, context, hashes, roles):
    _require(canonical_digest(document.get("subject")) == canonical_digest(context.get("subject")),
             "subject-mismatch")
    _require(document.get("inputs") == {role: hashes[role] for role in roles}, "evidence-mismatch")


def _prepare(controller, context):
    _require(type(context) is dict and context.get("group") == "quality-review"
             and context.get("kind") == "img2.quality-review-context", "group-mismatch")
    _require(context.get("policyVersion") == "route-quality-policy-v1", "policy-mismatch")
    hashes, documents = _read_bindings(controller, context)
    requirements = context.get("requirements")
    _require(type(requirements) is dict
             and canonical_digest(requirements) == canonical_digest(documents["quality-contract"]), "contract-mismatch")
    _require(requirements.get("rubricVersion") == "quality-review-rubric-v1", "rubric-mismatch")
    dimensions = requirements.get("dimensions")
    _require(type(dimensions) is list, "invalid-requirements")
    dimension_ids = [item if type(item) is str else item.get("id") if type(item) is dict else None
                     for item in dimensions]
    _require(all(type(item) is str for item in dimension_ids)
             and len(set(dimension_ids)) == len(dimension_ids)
             and _DIMENSIONS <= set(dimension_ids), "invalid-requirements")
    for dimension in dimensions:
        threshold = dimension.get("threshold", 3) if type(dimension) is dict else 3
        _require(_number(threshold) and 3 <= threshold <= 4, "invalid-requirements")
    features = requirements.get("criticalFeatures")
    _require(type(features) is list and all(type(item) is dict for item in features), "invalid-requirements")
    feature_ids = [item.get("id") for item in features]
    _require(all(type(item) is str and item for item in feature_ids)
             and len(set(feature_ids)) == len(feature_ids), "invalid-requirements")
    for feature in features:
        threshold = feature.get("threshold", 3)
        _require(_number(threshold) and 3 <= threshold <= 4, "invalid-requirements")
    gate, report, views = (documents[role] for role in ("gate-report", "quality-report", "view-set"))
    _bound(views, context, hashes, _PAIR_ROLES)
    _bound(gate, context, hashes, (*_PAIR_ROLES, "view-set"))
    _bound(report, context, hashes, (*_PAIR_ROLES, "view-set", "gate-report"))
    _require(type(gate.get("visionAllowed")) is bool
             and type(gate.get("hardGateFailures")) is list, "admission-missing")
    candidate = report.get("candidate")
    _require(type(candidate) is dict, "invalid-evidence")
    _require(candidate.get("gateResults") == gate.get("gateResults")
             and type(gate.get("gateResults")) is list and gate["gateResults"], "gate-mismatch")
    evidence = candidate.get("evidence")
    _require(type(evidence) is dict and all(evidence.get(field) == hashes[role] for field, role in (
        ("referenceSha256", "reference"), ("cameraSha256", "camera"), ("viewSetSha256", "view-set"))),
        "evidence-mismatch")
    assessment = assess_candidate(candidate)
    if not gate["visionAllowed"] or gate["hardGateFailures"]:
        raise DecisionError("vision-forbidden")
    captures, crops = views.get("views"), views.get("features")
    _require(type(captures) is list and type(crops) is list, "missing-evidence")
    available, view_roles = [], set()
    for view in captures:
        _require(type(view) is dict and set(view) == {"id", "role", "sha256"}, "invalid-evidence")
        _require(type(view["id"]) is int and 0 <= view["id"] <= 360
                 and view["id"] not in available, "invalid-evidence")
        _require(type(view["role"]) is str and view["role"] not in view_roles
                 and hashes.get(view["role"]) == view["sha256"], "evidence-mismatch")
        available.append(view["id"])
        view_roles.add(view["role"])
    observations = context.get("observations")
    _require(type(observations) is dict, "invalid-context")
    _require(observations.get("availableViews") == available and candidate.get("views") == available,
             "view-mismatch")
    required_views = requirements.get("requiredViews")
    _require(type(required_views) is list and all(type(view) is int for view in required_views), "invalid-requirements")
    _require({0, 90, 180, 270} <= set(required_views), "invalid-requirements")
    _require(set(required_views) <= set(available), "missing-views")
    crop_ids, crop_roles = [], set()
    for crop in crops:
        _require(type(crop) is dict and set(crop) == {
            "id", "referenceRole", "referenceSha256", "renderRole", "renderSha256"}, "invalid-evidence")
        _require(type(crop["id"]) is str and crop["id"] not in crop_ids, "invalid-evidence")
        crop_ids.append(crop["id"])
        roles = [crop["referenceRole"], crop["renderRole"]]
        _require(all(type(role) is str and role not in _REQUIRED_ROLES and role not in crop_roles for role in roles)
                 and roles[0] != roles[1], "invalid-evidence")
        for prefix in ("reference", "render"):
            _require(hashes.get(crop[prefix + "Role"]) == crop[prefix + "Sha256"], "evidence-mismatch")
        crop_roles.update(roles)
    _require(set(crop_ids) == set(feature_ids), "missing-critical-evidence")
    images = observations.get("images")
    _require(type(images) is list and all(type(image) is dict and set(image) == {"role", "asset"}
                                       for image in images), "missing-evidence")
    image_roles = [image["role"] for image in images]
    _require(all(type(role) is str for role in image_roles) and len(set(image_roles)) == len(image_roles)
             and {"reference", "render"} | crop_roles | {view["role"] for view in captures} <= set(image_roles),
             "missing-evidence")
    supplied = candidate.get("features")
    _require(type(supplied) is list and all(type(item) is dict for item in supplied), "missing-critical-evidence")
    critical = [item for item in supplied if item.get("tier") == "critical"]
    _require(len(critical) == len(feature_ids)
             and all(type(item.get("id")) is str for item in critical)
             and {item["id"] for item in critical} == set(feature_ids), "missing-critical-evidence")
    for feature in features:
        measured = next(item for item in critical if item["id"] == feature["id"])
        _require(measured.get("threshold") == feature.get("threshold", 3), "contract-mismatch")
        crop = next(item for item in crops if item["id"] == feature["id"])
        required_roles = feature.get("evidenceRoles", [])
        _require(type(required_roles) is list and all(type(role) is str for role in required_roles)
                 and {crop["referenceRole"], crop["renderRole"]} <= set(required_roles),
                 "missing-critical-evidence")
    for item in features + [item for item in dimensions if type(item) is dict]:
        needed_views = item.get("requiredViews", [])
        _require(type(needed_views) is list and all(type(view) is int for view in needed_views),
                 "invalid-requirements")
        _require(set(needed_views) <= set(available), "missing-views")
    for item in [requirements] + features + [item for item in dimensions if type(item) is dict]:
        roles = item.get("evidenceRoles", [])
        _require(type(roles) is list and all(type(role) is str for role in roles), "invalid-requirements")
        _require(set(roles) <= hashes.keys(), "missing-evidence")
    _require(not any(check["status"] == "unevaluated" for check in assessment["checks"]), "missing-measurements")
    return assessment


def _result(action, reason, assessment=None, decision=None, unevaluated=None):
    result = {"action": action, "status": "pass" if action == "continue" else
              "unevaluated" if action == "request-input" else "fail",
              "deliverable": action == "continue", "reasonCode": reason,
              "assessment": assessment, "decision": decision, "unevaluated": unevaluated or []}
    if decision and "loginCommand" in decision:
        result["loginCommand"] = decision["loginCommand"]
    return result


def _unevaluated(context):
    """Name withheld findings; a missing capture never becomes a hidden-side pass."""
    if type(context) is not dict or type(context.get("requirements")) is not dict:
        return ["visual-review"]
    requirements = context["requirements"]
    result = ["visual-review"]
    for prefix, name in (("critical:", "criticalFeatures"), ("dimension:", "dimensions")):
        items = requirements.get(name, [])
        if type(items) is list:
            for item in items:
                identifier = item.get("id") if type(item) is dict else item
                if type(identifier) is str:
                    result.append(prefix + identifier)
    views = requirements.get("requiredViews", [])
    if type(views) is list:
        result.extend("view:" + str(view) for view in views if type(view) is int)
    return result


def review_quality(controller, context, *, provider=None, fallback=None, availability=None,
                   consent=None, request_token_limit=64000):
    """Return a base-owned continue/refine/request-input action and raw evidence.

    No callback runs before fresh deterministic admission and matched evidence.
    The supplied controller owns all auth, uncertainty, deadlines and budgets;
    this adapter creates no replacement controller or allowance. Main owns entry
    point wiring and persistence of the returned decision reference/action.
    """
    context = deepcopy(context)
    assessment, decision = None, None
    try:
        assessment = _prepare(controller, context)
        decision = controller.consult(context, provider=provider, fallback=fallback,
                                      availability=availability, consent=consent,
                                      request_token_limit=request_token_limit)
        decision = controller.consume(decision, context)
    except DecisionError as exc:
        return _result("request-input", exc.reason_code, assessment, decision, _unevaluated(context))
    if decision["status"] != "answered":
        return _result("request-input", decision["reasonCode"], assessment, decision,
                       [key for key, answer in decision["answers"].items()
                        if answer.get("type") == "unevaluated" or answer.get("choice") == "uncertain"]
                       or _unevaluated(context))
    answers = decision["answers"]
    semantic_failure = False
    for dimension in context["requirements"]["dimensions"]:
        item = {"id": dimension} if type(dimension) is str else dimension
        answer = answers["dimension:" + item["id"]]
        semantic_failure |= answer["score"] < item.get("threshold", 3)
    for feature in context["requirements"]["criticalFeatures"]:
        semantic_failure |= answers["critical:" + feature["id"]]["choice"] != "pass"
    if not assessment["passed"]:
        return _result("refine", "independent-quality-failed", assessment, decision)
    if semantic_failure:
        return _result("refine", "semantic-quality-failed", assessment, decision)
    return _result("continue", "quality-passed", assessment, decision)
