"""Fail-closed routing for the authoring tracks the BASE itself implements.

That is one track now: `weapon-v1.4`. `character-v1.5` left with the humanoid template it selected
(OpenSpec change `extract-character-sculpt-into-the-plugin`); a character run reaches its content
through domain resolution and `merge_spec_augmentation`, not through a track name the base holds.

`character` and `hybrid` stay in VALID_KINDS, and the distinction is the point of this module. A
KIND is what the classifier saw; a TRACK is what this repo can build from it. Dropping `character`
from the vocabulary would make a correct classification read as `malformed-classification` --
"part object, part character, I cannot decide" and "a character" would both come back as corrupt
input. Both still fail closed, but they now fail closed with an accurate reason, which is the whole
value of a gate that refuses.
"""

from __future__ import annotations

from typing import Any, Final


CONFIDENCE_THRESHOLD: Final[float] = 0.82
TRACK_BY_KIND: Final[dict[str, str]] = {
    "weapon": "weapon-v1.4",
}
VALID_TRACKS: Final[frozenset[str]] = frozenset(TRACK_BY_KIND.values())
# What a classifier may report. Deliberately WIDER than TRACK_BY_KIND: see the module docstring.
VALID_KINDS: Final[frozenset[str]] = frozenset({"weapon", "character", "hybrid", "unknown"})
# `provider` is the source for a kind this repo classifies but has no track for, answered by an
# installed domain plugin. Its record carries `track: None` -- deliberately, because there IS no
# base track and inventing a name for one would be the base naming a domain, which is exactly what
# the registry exists to stop.
VALID_SOURCES: Final[frozenset[str]] = frozenset({"explicit", "classification", "legacy", "provider"})
VALID_STATUSES: Final[frozenset[str]] = frozenset({"resolved", "request-input"})


def _fallback_classification(reason: str) -> dict[str, Any]:
    return {
        "kind": "unknown",
        "confidence": 0.0,
        "evidenceRefs": [f"pipeline-routing:{reason}"],
        "provider": "pipeline-routing",
        "version": "1",
    }


def _explicit_classification(track: str) -> dict[str, Any]:
    # Only reachable for a track in VALID_TRACKS, which the caller has already checked.
    kind = next(k for k, t in TRACK_BY_KIND.items() if t == track)
    return {
        "kind": kind,
        "confidence": 1.0,
        "evidenceRefs": [f"pipeline-routing:explicit:{track}"],
        "provider": "pipeline-routing-cli",
        "version": "1",
    }


def _normalize_classification(classification: Any) -> tuple[dict[str, Any], list[str]]:
    if classification is None:
        return _fallback_classification("missing-classification"), []
    if not isinstance(classification, dict):
        return _fallback_classification("malformed-classification"), ["classification is malformed"]
    kind = classification.get("kind")
    confidence = classification.get("confidence")
    refs = classification.get("evidenceRefs")
    provider = classification.get("provider")
    version = classification.get("version")
    malformed = (
        kind not in VALID_KINDS
        or not isinstance(confidence, (int, float))
        or isinstance(confidence, bool)
        or not 0.0 <= confidence <= 1.0
        or not isinstance(refs, list)
        or not refs
        or not all(isinstance(ref, str) and ref for ref in refs)
        or not isinstance(provider, str)
        or not provider
        or not isinstance(version, str)
        or not version
    )
    if malformed:
        return _fallback_classification("malformed-classification"), ["classification is malformed"]
    return {
        "kind": kind,
        "confidence": float(confidence),
        "evidenceRefs": list(refs),
        "provider": provider,
        "version": version,
    }, []


def classification_from_cs2_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Translate the CS2 authoritative record into the shared routing classification."""
    record = manifest.get("classification")
    if not isinstance(record, dict):
        return _fallback_classification("cs2-manifest-missing-classification")
    return {
        "kind": "weapon",
        "confidence": record.get("confidence"),
        "evidenceRefs": record.get("evidenceRefs"),
        "provider": record.get("provider"),
        "version": record.get("version"),
    }


def provider_may_resolve(kind: Any, confidence: Any, provider_domain: Any) -> bool:
    """Whether an installed provider is allowed to clear this classification's one conflict.

    Shared by `resolve_pipeline_routing` and `validate_pipeline_routing` deliberately. They had
    drifted the moment the branch was written: the resolver checked four things, the validator
    checked the record's SHAPE and nothing else, so a hand-edited spec claiming
    `source: "provider"` walked a 0.40-confidence hybrid through as `resolved` -- the exact
    "needs a human" case this module exists to stop, and a way around the track check that was
    impossible before the provider branch existed.

    A provider answers exactly ONE conflict: "this kind has no base authoring track". It cannot
    vouch for a kind nobody is confident about, a hybrid that needs a person, or a kind the base
    can build perfectly well itself.
    """
    return (
        isinstance(provider_domain, str)
        and bool(provider_domain)
        and kind == provider_domain
        and kind not in TRACK_BY_KIND
        and kind not in {"hybrid", "unknown"}
        and isinstance(confidence, (int, float))
        and not isinstance(confidence, bool)
        and confidence >= CONFIDENCE_THRESHOLD
    )


def resolve_pipeline_routing(
    *,
    explicit_track: str | None = None,
    classification: Any = None,
    legacy_cs2: bool = False,
    provider_domain: str | None = None,
) -> dict[str, Any]:
    """Resolve one supported track or return request-input without guessing a template.

    `provider_domain` is the id of a domain plugin that has ALREADY supplied this kind's content
    (a resolved `--domain` plus the artifact it published). Without it, a confident `character`
    classification came back `request-input` whatever the caller did, so the conflict's own advice
    -- "its domain plugin supplies the content through --domain character" -- could be followed
    exactly and reproduce the message. That is advice that cannot succeed, the failure the
    withdrawal table in `domains/__init__.py` was written to avoid, repeated one file over.

    Resolving it HERE rather than in the caller keeps `status` and `source` owned by the function
    that defines them: a caller that patched the record afterwards would be writing a field whose
    invariants live in this module.
    """
    if legacy_cs2:
        resolved_classification = {
            "kind": "weapon",
            "confidence": 1.0,
            "evidenceRefs": ["legacy:cs2Intake"],
            "provider": "legacy-cs2-intake",
            "version": "1",
        }
        if explicit_track is not None and explicit_track != "weapon-v1.4":
            return {
                "version": 1,
                "track": explicit_track,
                "source": "explicit",
                "status": "request-input",
                "classification": resolved_classification,
                "conflicts": [
                    f"explicit track {explicit_track!r} contradicts legacy CS2 weapon routing"
                ],
            }
        return {
            "version": 1,
            "track": "weapon-v1.4",
            "source": "legacy",
            "status": "resolved",
            "classification": resolved_classification,
            "conflicts": [],
        }

    normalized, conflicts = _normalize_classification(classification)
    if explicit_track is not None and explicit_track not in VALID_TRACKS:
        conflicts.append("explicit track is invalid")
        explicit_track = None
    if explicit_track is not None and classification is None:
        normalized = _explicit_classification(explicit_track)

    kind = normalized["kind"]
    confidence = normalized["confidence"]
    reliable_kind = kind in TRACK_BY_KIND and confidence >= CONFIDENCE_THRESHOLD
    requested_track = explicit_track or TRACK_BY_KIND.get(kind, "weapon-v1.4")
    if kind in {"hybrid", "unknown"}:
        conflicts.append(f"classification kind {kind!r} requires input")
    elif kind not in TRACK_BY_KIND:
        # A kind this repo classifies but has no track for. Naming the remedy matters: without it
        # the record is `request-input` with an EMPTY conflicts list, and the caller's error message
        # is the literal string "pipeline routing requires input: ".
        conflicts.append(
            f"classification kind {kind!r} has no base authoring track; its domain plugin supplies "
            f"the content through --domain {kind} and a spec-augmentation artifact"
        )
    # NOT an `elif` on the track branch above. A low-confidence `character` has TWO problems, and
    # chaining them reported only the first -- so the record said "its domain plugin supplies the
    # content", implying the provider was the whole answer, while the real blocker was that nobody
    # is sure it is a character at all. A provider cannot answer that one.
    if confidence < CONFIDENCE_THRESHOLD and kind not in {"hybrid", "unknown"}:
        conflicts.append(f"classification confidence {confidence:.2f} is below {CONFIDENCE_THRESHOLD:.2f}")
    if explicit_track is not None and reliable_kind and TRACK_BY_KIND[kind] != explicit_track:
        conflicts.append(f"explicit track {explicit_track!r} contradicts reliable classification {kind!r}")

    # The provider answers exactly one conflict: "this kind has no base authoring track". It cannot
    # clear a malformed classification, a low-confidence one, or a hybrid/unknown that needs a human
    # -- so the claim has to match the kind, and the kind has to be one the base genuinely lacks.
    answered_by_provider = (
        provider_may_resolve(kind, confidence, provider_domain)
        # An explicit track is a request this branch would silently drop -- it returns
        # `track: None`, so the caller's choice would vanish with no conflict recorded. Let the
        # normal path handle the contradiction instead of swallowing it.
        and explicit_track is None
        and not [c for c in conflicts if "has no base authoring track" not in c]
    )
    if answered_by_provider:
        return {
            "version": 1,
            "track": None,
            "source": "provider",
            "status": "resolved",
            "classification": normalized,
            "conflicts": [],
            "provider": provider_domain,
        }

    status = "resolved" if reliable_kind and not conflicts else "request-input"
    source = "explicit" if explicit_track is not None else "classification"
    return {
        "version": 1,
        "track": requested_track,
        "source": source,
        "status": status,
        "classification": normalized,
        "conflicts": conflicts,
    }


def validate_pipeline_routing(routing: Any) -> list[str]:
    """Return contract errors for a persisted routing record."""
    if not isinstance(routing, dict):
        return ["pipelineRouting must be an object"]
    errors: list[str] = []
    if routing.get("version") != 1:
        errors.append("pipelineRouting.version must be 1")
    if routing.get("source") == "provider":
        # A provider-resolved record has no base track by construction. It must say so with None
        # rather than borrow a track name, and it must name the provider that answered.
        if routing.get("track") is not None:
            errors.append("pipelineRouting.track must be null when source is provider")
        if not isinstance(routing.get("provider"), str) or not routing.get("provider"):
            errors.append("pipelineRouting.provider must name the domain that resolved it")
    elif routing.get("track") not in VALID_TRACKS:
        errors.append("pipelineRouting.track must be one of " + ", ".join(sorted(VALID_TRACKS)))
    if routing.get("source") not in VALID_SOURCES:
        errors.append("pipelineRouting.source must be explicit, classification, or legacy")
    if routing.get("status") not in VALID_STATUSES:
        errors.append("pipelineRouting.status must be resolved or request-input")
    classification = routing.get("classification")
    normalized, classification_errors = _normalize_classification(classification)
    if classification_errors:
        errors.append("pipelineRouting.classification is malformed")
    if not isinstance(classification, dict) or classification != normalized:
        errors.append("pipelineRouting.classification must use the shared classification contract")
    if routing.get("source") == "provider" and isinstance(routing.get("provider"), str) \
            and routing["provider"] and not provider_may_resolve(
                normalized["kind"], normalized["confidence"], routing["provider"]):
        # Shape alone is not authority, and this check needs the NORMALIZED classification, so it
        # sits below rather than beside the shape checks above. Without it the provider branch is a
        # hole in the fail-closed guarantee: the same record, hand-edited, admits anything -- a
        # 0.40-confidence hybrid came back `resolved` with no errors at all.
        errors.append(
            f"pipelineRouting.provider {routing['provider']!r} cannot resolve a "
            f"{normalized['kind']!r} classification at confidence {normalized['confidence']:.2f}"
        )
    conflicts = routing.get("conflicts")
    if not isinstance(conflicts, list) or not all(isinstance(conflict, str) for conflict in conflicts):
        errors.append("pipelineRouting.conflicts must be a list")
    elif routing.get("status") == "resolved" and conflicts:
        errors.append("resolved pipelineRouting cannot contain conflicts")
    return errors
