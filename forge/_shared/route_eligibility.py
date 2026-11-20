"""Filter program-discovered route candidates before any model consultation.

This module performs no provider discovery and executes no dependency probes.
The caller must independently resolve typed edges, pin card bytes and obtain
program-owned dependency and blocking-gate results. These values are not model
observations and must never be accepted from a route card or provider response.
The result is eligibility only; it does not authorize execution or delivery.
"""
from __future__ import annotations

import math
import re

_ID = re.compile(r'^[a-z][a-z0-9-]{0,95}$')
_OUTPUTS = {'2d-scene', '3d-scene', '3d-object', 'rigged-3d', 'spec-augmentation'}
_OBSERVATIONS = {'enclosure', 'roomCount', 'articulation', 'flatness', 'multiplicity', 'stylization'}


def _allowed(value, options):
    """Match only exact values and the D3 numeric >N comparator; unknown fails closed."""
    if value is None or value == 'unknown' or type(options) is not list or not options:
        return False
    for option in options:
        if type(option) is str and re.fullmatch(r'>[0-9]+', option):
            if type(value) is int and value > int(option[1:]):
                return True
            if type(value) is float and math.isfinite(value) and value > int(option[1:]):
                return True
        elif type(value) is type(option) and value == option:
            return True
    return False


def evaluate_candidate(card, *, expected_output, roles, intent, observations,
                       provider_status, dependencies, blocking_gate_produced):
    """Return deterministic rejection reasons; all arguments except card are trusted probes.

    provider_status is the harness edge resolution (answered/ambiguous/data-fault),
    not a claim in the card. dependencies maps each declared probe to a Boolean
    independently observed by the caller; None/missing means not established.
    blocking_gate_produced is True only if a declared blocking gate has an actual
    runnable producer. 2D is eligible only for explicit expected_output='2d'.
    """
    reasons = []
    if (type(card) is not dict or card.get('kind') != 'img2.route-card'
            or type(card.get('version')) is not int or card['version'] != 1
            or type(card.get('outputKind')) is not str or card['outputKind'] not in _OUTPUTS
            or type(card.get('costClass')) is not str
            or card['costClass'] not in {'local', 'heavy', 'licensed', 'paid'}):
        return {'eligible': False, 'reasons': ['invalid-route-card']}
    if type(expected_output) is not str or expected_output not in {'2d', '3d'}:
        return {'eligible': False, 'reasons': ['invalid-output-request']}
    if (expected_output == '2d') != (card['outputKind'] == '2d-scene'):
        reasons.append('output-kind-mismatch')
    if provider_status != 'answered':
        reasons.append('provider-ambiguous' if provider_status == 'ambiguous' else 'provider-unavailable')
    if blocking_gate_produced is not True:
        reasons.append('no-blocking-gate')
    if (type(roles) is not list or type(intent) is not list or type(observations) is not dict
            or any(type(item) is not str or not _ID.fullmatch(item) for item in roles + intent)):
        reasons.append('invalid-observations')
        return {'eligible': False, 'reasons': reasons}
    requires = card.get('requires', {})
    not_for = card.get('notFor', {})
    deps = card.get('deps', [])
    if (type(requires) is not dict or set(requires) - {'roles', 'intent', 'observations'}
            or type(not_for) is not dict or set(not_for) - {'roles', 'intent', 'observations'}
            or type(deps) is not list or type(dependencies) is not dict):
        reasons.append('invalid-route-card')
        return {'eligible': False, 'reasons': reasons}
    for row in deps:
        if type(row) is not dict or set(row) != {'probe'} or type(row['probe']) is not str or not row['probe']:
            reasons.append('invalid-route-card')
            continue
        if dependencies.get(row['probe']) is not True:
            reasons.append('dependency-missing:' + row['probe'])
    for key, actual in (('roles', roles), ('intent', intent)):
        required, excluded = requires.get(key, []), not_for.get(key, [])
        if (type(required) is not list or type(excluded) is not list
                or any(type(x) is not str or not _ID.fullmatch(x) for x in required + excluded)):
            reasons.append('invalid-route-card')
            continue
        if not all(value in actual for value in required):
            reasons.append('missing-required-' + key)
        if any(value in actual for value in excluded):
            reasons.append('excluded-' + key)
    for group, exclusion in ((requires.get('observations', {}), False), (not_for.get('observations', {}), True)):
        if type(group) is not dict or set(group) - _OBSERVATIONS:
            reasons.append('invalid-route-card')
            continue
        for key, options in group.items():
            if type(options) is not list or not options or any(type(v) not in (str, int, float, bool) for v in options):
                reasons.append('invalid-route-card')
                continue
            value = observations.get(key)
            if value is None or value == 'unknown':
                reasons.append('unknown-observation:' + key)
            elif _allowed(value, options) == exclusion:
                reasons.append(('excluded-observation:' if exclusion else 'missing-required-observation:') + key)
    return {'eligible': not reasons, 'reasons': list(dict.fromkeys(reasons))}
