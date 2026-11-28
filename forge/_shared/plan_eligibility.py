"""Preflight strategy and repair candidates against program-owned execution scope.

Plans are data, not commands. The caller obtains the allowlists, authorisations,
pass lock and rig capability from trusted code and verified evidence, never from
the candidate or a model reply. This function does not authorize mutation; the
shared controller must bind and persist a selected decision first.
"""
from __future__ import annotations

import copy
import re

_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$')
_SHA = re.compile(r'^[0-9a-f]{64}$')




def filter_plans(plans, *, pass_id, pass_unlocked, components, operations, field_paths,
                 authorized_sources, source_operations, available_dependencies, rig_required, rig_available):
    """Return eligible candidates and per-id reasons without executing anything.

    Each candidate has exactly {id,passId,components,operations,fieldPaths,
    materialSources,dependencies,rigHandoff}. Operation and field path entries
    must be from caller-supplied controlled allowlists. materialSources lists
    SHA-256s already independently admitted/authorized by the caller. The base
    supplies source_operations for operations that require external asset provenance.
    The caller remains responsible for enforcing actual deltas and source bytes on apply.
    """
    if (type(plans) is not list or type(pass_id) is not str or not _ID.fullmatch(pass_id)
            or type(pass_unlocked) is not bool or type(rig_required) is not bool
            or type(rig_available) is not bool
            or any(type(values) not in (set, frozenset) for values in
                   (components, operations, field_paths, authorized_sources, source_operations, available_dependencies))
            or not source_operations <= operations):
        raise ValueError('invalid-trusted-scope')
    eligible, rejected, seen = [], [], set()
    keys = {'id', 'passId', 'components', 'operations', 'fieldPaths',
            'materialSources', 'dependencies', 'rigHandoff'}
    for plan in plans:
        ident = plan.get('id') if type(plan) is dict else None
        if type(ident) is not str or not _ID.fullmatch(ident) or ident in seen:
            raise ValueError('invalid-or-duplicate-candidate-id')
        seen.add(ident)
        reasons = []
        if set(plan) != keys:
            reasons.append('invalid-plan-shape')
        if plan.get('passId') != pass_id or not pass_unlocked:
            reasons.append('locked-or-wrong-pass')
        for name, allowed in (('components', components), ('operations', operations),
                              ('fieldPaths', field_paths), ('dependencies', available_dependencies)):
            values = plan.get(name)
            if type(values) is not list or any(type(v) is not str or not v for v in values) or len(set(values)) != len(values):
                reasons.append('invalid-' + name)
            elif name in {'components', 'operations'} and not values:
                reasons.append('invalid-' + name)
            elif not set(values) <= allowed:
                reasons.append('unsupported-' + name)
        sources = plan.get('materialSources')
        if (type(sources) is not list or any(type(v) is not str or not _SHA.fullmatch(v) for v in sources)
                or len(set(sources)) != len(sources)):
            reasons.append('invalid-material-sources')
        elif not set(sources) <= authorized_sources:
            reasons.append('unauthorized-material-source')
        if (type(plan.get('operations')) is list and type(sources) is list
                and any(type(op) is str and op in source_operations for op in plan['operations']) and not sources):
            reasons.append('material-source-required')
        rig = plan.get('rigHandoff')
        if type(rig) is not bool:
            reasons.append('invalid-rig-handoff')
        elif rig_required and (not rig or not rig_available):
            reasons.append('missing-rig-handoff')
        if reasons:
            rejected.append({'id': ident, 'reasons': reasons})
        else:
            eligible.append(copy.deepcopy(plan))
    return {'eligible': eligible, 'rejected': rejected}
