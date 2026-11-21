"""Placement Choices use the existing build-strategy lifecycle and shared budget.

Options are program-enumerated transforms, never provider-authored instructions.
The caller owns approval of class priors, camera/depth/support producers and
candidate scope. Journal verification authenticates the decision, not those
upstream producers. No result here completes a child or accepts a scene.
"""
from __future__ import annotations

from copy import deepcopy

from .decision_control import DecisionController, DecisionError


def placement_context(subject, bindings, *, question, options, observations=None):
    """Build one bounded size/yaw consultation; full binding validation is live."""
    if question not in {"size", "yaw"} or type(subject) is not dict:
        raise DecisionError("invalid-placement-context")
    context = {
        "kind": "img2.build-strategy-context", "version": 1, "group": "build-strategy",
        "subject": dict(deepcopy(subject), passId="placement-" + question),
        "bindings": deepcopy(bindings), "candidates": deepcopy(options),
        "observations": deepcopy(observations) if observations is not None else {},
        "requirements": {"criticalFeatures": [], "requiredViews": [],
                         "placement": {"question": question}},
        "policyVersion": "placement-policy-v1", "questionSetVersion": "strategy-questions-v1",
        "requestedModel": "jev-1.14.0",
    }
    if type(bindings) is not list or type(options) is not list or not options:
        raise DecisionError("invalid-placement-context")
    DecisionController._placement(context)
    return context


def consume_placement(verify, context, record):
    """Consume only through the live controller/transaction verifier supplied by base.

    ``verify`` is workspace_transaction's verifier, or an equivalent closure over
    controller.consume(..., consent=live_consent). Neither a saved outcome nor a
    provider string is authority. A model answer retains its journalled outage.
    """
    if not callable(verify):
        raise DecisionError("placement-authority-required")
    context = deepcopy(context)
    DecisionController._placement(context)
    if context.get("group") != "build-strategy" or "placement" not in context.get("requirements", {}):
        raise DecisionError("invalid-placement-context")
    current = verify(deepcopy(record), context)
    if current.get("status") != "answered":
        raise DecisionError(current.get("reasonCode", "placement-answer-unavailable"))
    choice = current["answers"]["selection"]["choice"]
    selected = [candidate for candidate in context["candidates"] if candidate["id"] == choice]
    if len(selected) != 1:
        raise DecisionError("invalid-placement-candidate")
    bindings = {binding["role"]: binding for binding in context["bindings"]}
    decider = current.get("decider", "")
    if not isinstance(decider, str) or not decider.startswith(("jev:", "model:")):
        raise DecisionError("invalid-placement-decider")
    answer = {
        "kind": "img2.placement-answer", "version": 1,
        "objectId": context["subject"]["objectId"],
        "question": context["requirements"]["placement"]["question"],
        "value": deepcopy(selected[0]["value"]), "decider": decider,
        "reasonCode": "answered", "decisionRef": current["recordId"],
        "inventorySha256": bindings["inventory"]["sha256"],
        "referenceSha256": bindings["reference"]["sha256"],
        "artifactSha256": bindings["artifact"]["sha256"],
    }
    if decider.startswith("model:"):
        reason = current.get("unavailableReason")
        if not reason:
            raise DecisionError("fallback-unproven")
        answer["reasonCode"] = reason
    return answer


def consult_placement(controller, context, **callbacks):
    """Consult Jev first; only the controller may authorize a fallback."""
    DecisionController._placement(context)
    if context.get("group") != "build-strategy" or "placement" not in context.get("requirements", {}):
        raise DecisionError("invalid-placement-context")
    record = controller.consult(context, **callbacks)
    consent = callbacks.get("consent")
    verify = lambda value, original: controller.consume(value, original, consent=consent)
    answer = None
    if record["status"] == "answered":
        answer = consume_placement(verify, context, record)
    return {"context": deepcopy(context), "record": record, "answer": answer}
