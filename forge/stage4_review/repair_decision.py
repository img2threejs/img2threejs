"""Scoped repair authority, not a worker, renderer, or evidence authenticator.

The host supplies independently admitted evidence/provenance and program-owned
scope. Every revision must have separate immutable files: workers must not edit
input bindings. This module only journals decisions/applications through the
shared controller; it never changes a spec, source, gate, or incumbent artifact.
Real rebuilt measurements and semantic review must be produced outside it.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path, PurePosixPath

from _shared.decision_control import DecisionError, canonical_digest
from _shared.decision_quality import assess_candidate, compare_repair
from _shared.plan_eligibility import filter_plans

_HYPOTHESES = {'camera', 'geometry', 'material', 'lighting', 'rig', 'placement', 'spec', 'input-evidence'}
_ACTIONS = {'refine-spec', 'refine-code', 'request-input', 'stop'}
_FAULTS = {'evidence-integrity', 'stale-evidence', 'provenance', 'permission'}
_PLAN_SCOPE = ('pass_id', 'pass_unlocked', 'components', 'operations', 'field_paths',
               'authorized_sources', 'source_operations', 'available_dependencies', 'rig_required', 'rig_available')
_FORBIDDEN_OPERATIONS = {'disable-gate', 'lower-threshold', 'hide-component',
                         'remove-required-component', 'unlock-pass', 'change-policy'}
_FORBIDDEN_FIELDS = {'gates', 'thresholds', 'policy', 'qualityPolicy', 'criticalFeatures',
                     'requiredComponents', 'permissions', 'passLocks', 'provenance'}


def _blocked(reason, *, stop=False, rejected=None):
    return {'status': 'stop' if stop else 'request-input', 'reasonCode': reason,
            'deliverable': False, 'rejected': rejected or []}


def _hashes(bindings):
    return [{'role': item['role'], 'sha256': item['sha256']} for item in bindings]


def _bound_failure(workspace, subject, bindings, failure):
    """Read and hash every file, then match the actual gate envelope to input."""
    if type(bindings) is not list or not bindings or type(failure) is not dict:
        raise DecisionError('evidence-integrity')
    if set(failure) != {'subject', 'bindings', 'report', 'faults'}:
        raise DecisionError('evidence-integrity')
    if failure['subject'] != subject:
        raise DecisionError('stale-evidence')
    if type(failure['faults']) is not list or any(type(fault) is not str for fault in failure['faults']):
        raise DecisionError('evidence-integrity')
    for fault in failure['faults']:
        if fault in _FAULTS:
            raise DecisionError(fault)
    root = Path(workspace).resolve()
    roles, gate = set(), None
    for binding in bindings:
        if type(binding) is not dict or set(binding) != {'role', 'path', 'sha256'}:
            raise DecisionError('evidence-integrity')
        role, relative = binding['role'], binding['path']
        if type(role) is not str or role in roles or type(relative) is not str:
            raise DecisionError('evidence-integrity')
        roles.add(role)
        if (not relative or '\\' in relative or PurePosixPath(relative).is_absolute()
                or any(part in {'', '.', '..'} for part in relative.split('/'))):
            raise DecisionError('evidence-integrity')
        path = root
        for part in relative.split('/'):
            path = path / part
            if path.is_symlink():
                raise DecisionError('evidence-integrity')
        try:
            data = path.read_bytes()
        except OSError:
            raise DecisionError('evidence-integrity') from None
        if hashlib.sha256(data).hexdigest() != binding['sha256']:
            raise DecisionError('stale-evidence')
        if role == 'gate-report':
            try:
                gate = json.loads(data)
            except (ValueError, UnicodeError):
                raise DecisionError('evidence-integrity') from None
    if not {'gate-report', 'source', 'spec', 'reference', 'camera', 'view-set'} <= roles:
        raise DecisionError('evidence-integrity')
    if canonical_digest(gate) != canonical_digest(failure):
        raise DecisionError('evidence-integrity')
    inputs = [binding for binding in bindings if binding['role'] != 'gate-report']
    if failure['bindings'] != inputs:
        raise DecisionError('stale-evidence')
    evidence = failure['report'].get('evidence', {}) if type(failure['report']) is dict else {}
    for role, field in (('reference', 'referenceSha256'), ('camera', 'cameraSha256'),
                        ('view-set', 'viewSetSha256')):
        binding = next(item for item in inputs if item['role'] == role)
        if evidence.get(field) != binding['sha256']:
            raise DecisionError('stale-evidence')


def prepare_repair(controller, *, subject, bindings, failure, candidates, scope, criteria,
                   requirements, policy_version='repair-policy-v1', requested_model='jev-1.14.0',
                   provider=None, fallback=None, availability=None, consent=None,
                   request_token_limit=64000):
    """Preflight, consult and consume a selection without authorizing mutation.

    ``failure`` is the exact bound gate-report JSON envelope
    {subject, bindings: [all non-gate bindings], report, faults: []}.
    ``scope`` contains filter_plans' keyword arguments plus ``permissions`` (set),
    ``remainingTrials`` (int), ``evidenceAdmitted``, ``provenanceVerified`` and
    ``permissionGranted`` (bools). These facts come from trusted base code, never
    a provider. The base independently computes ``source_operations`` (a subset
    of ``operations``) for operations requiring authorized external asset hashes;
    neither a proposal nor an advisory may declare its own provenance exemption.
    Candidates are {id, hypothesis, action, plan, permissions}; a mutation plan
    uses the shared plan schema, while stop/input use plan=None. Criteria are
    frozen in the context before consultation and application.
    """
    for key, reason in (('evidenceAdmitted', 'evidence-integrity'),
                        ('provenanceVerified', 'provenance'), ('permissionGranted', 'permission')):
        if scope.get(key) is not True:
            return _blocked(reason)
    try:
        _bound_failure(controller.workspace, subject, bindings, failure)
    except DecisionError as exc:
        return _blocked(exc.reason_code)
    if scope.get('pass_id') != subject.get('passId') or scope.get('pass_unlocked') is not True:
        return _blocked('locked-or-wrong-pass')
    if type(scope.get('remainingTrials')) is not int or scope['remainingTrials'] <= 0:
        return _blocked('correction-budget-exhausted', stop=True)
    if type(scope.get('permissions')) not in (set, frozenset):
        raise ValueError('invalid-trusted-permissions')
    before = failure['report']
    if assess_candidate(before)['status'] != 'fail':
        return _blocked('fresh-failed-gate-required')
    # Comparing a report with itself validates the complete frozen criterion;
    # a legitimate failure is comparable but has insufficient improvement.
    if compare_repair(before, before, criteria)['status'] == 'unevaluated':
        return _blocked('missing-or-incomparable-repair-criteria')
    if type(candidates) is not list:
        raise ValueError('invalid-repair-candidates')
    eligible, rejected, seen = [], [], set()
    for candidate in candidates:
        ident = candidate.get('id') if type(candidate) is dict else None
        if type(ident) is not str or not ident or ident in seen:
            raise ValueError('invalid-or-duplicate-candidate-id')
        seen.add(ident)
        reasons = []
        if (set(candidate) != {'id', 'hypothesis', 'action', 'plan', 'permissions'}
                or candidate.get('hypothesis') not in _HYPOTHESES
                or candidate.get('action') not in _ACTIONS):
            reasons.append('invalid-repair-candidate')
        permissions = candidate.get('permissions')
        if (type(permissions) is not list or any(type(value) is not str for value in permissions)
                or not set(permissions) <= scope['permissions']):
            reasons.append('permission')
        required_permission = {'refine-spec': 'write-spec', 'refine-code': 'write-code'}.get(candidate.get('action'))
        if required_permission and (required_permission not in scope['permissions']
                                    or type(permissions) is not list or required_permission not in permissions):
            reasons.append('permission')
        plan = candidate.get('plan')
        if candidate.get('action') in {'refine-spec', 'refine-code'}:
            if type(plan) is not dict or plan.get('id') != ident:
                reasons.append('invalid-repair-plan')
            else:
                result = filter_plans([plan], **{key: scope[key] for key in _PLAN_SCOPE})
                reasons.extend(reason for item in result['rejected'] for reason in item['reasons'])
                if result['eligible'] and (
                        any(value in _FORBIDDEN_OPERATIONS for value in plan['operations'])
                        or any(value.replace('/', '.').split('.')[0] in _FORBIDDEN_FIELDS
                               for value in plan['fieldPaths'])):
                    reasons.append('forbidden-repair-action')
        elif plan is not None:
            reasons.append('non-mutating-action-has-plan')
        if reasons:
            rejected.append({'id': ident, 'reasons': reasons})
        else:
            eligible.append(deepcopy(candidate))
    if not eligible:
        return _blocked('no-admissible-repair', rejected=rejected)
    context = {'kind': 'img2.repair-context', 'version': 1, 'group': 'repair',
               'subject': deepcopy(subject), 'bindings': deepcopy(bindings), 'candidates': eligible,
               'observations': {'before': deepcopy(before), 'faults': deepcopy(failure['faults']),
                                'criteria': deepcopy(criteria), 'remainingTrials': scope['remainingTrials']},
               'requirements': deepcopy(requirements), 'policyVersion': policy_version,
               'questionSetVersion': 'repair-decision-questions-v1', 'requestedModel': requested_model}
    # Persist exact execution scope as JSON, with deterministic set ordering.
    context['requirements']['repairScope'] = {
        key: sorted(value) if isinstance(value, (set, frozenset)) else deepcopy(value)
        for key, value in scope.items()}
    try:
        record = controller.consult(context, provider=provider, fallback=fallback,
                                    availability=availability, consent=consent,
                                    request_token_limit=request_token_limit)
        record = controller.consume(record, context, consent=consent)
    except DecisionError as exc:
        return _blocked(exc.reason_code, rejected=rejected)
    result = {'status': 'request-input', 'reasonCode': record['reasonCode'],
              'deliverable': False, 'context': context, 'record': record, 'rejected': rejected}
    if record['status'] == 'answered':
        selected = next(item for item in eligible if item['id'] == record['answers']['selection']['choice'])
        result.update(status=selected['action'], selected=deepcopy(selected))
    # Valid uncertainty stays request-input; it is never a model override or
    # implicit permission to apply a multi-trial selection.
    return result


def _selected(controller, selection, *, consent=None):
    context = selection['context']
    record = controller.consume(selection['record'], context, consent=consent)
    if record['status'] != 'answered':
        raise DecisionError('application-not-authorized')
    candidate = next(item for item in context['candidates']
                     if item['id'] == record['answers']['selection']['choice'])
    if candidate['action'] not in {'refine-spec', 'refine-code'}:
        raise DecisionError('application-not-authorized')
    return context, record, candidate


def begin_repair(controller, selection, trial_revision, *, consent=None):
    """Authorize one successor, using live host consent for fresh-process resume.

    A new controller requires a program-owned ``consent`` callback; persisted
    selections are never themselves consent. Omission keeps the shared
    controller's same-process live callback behavior and otherwise fails closed.
    """
    _, record, _ = _selected(controller, selection, consent=consent)
    return controller.begin_application(record, trial_revision, consent=consent)


def finish_repair(controller, selection, *, trial_revision, after, after_subject, after_bindings,
                  consent=None):
    """Compare independently produced successor evidence, then journal retention.

    ``after`` uses the same bound envelope as ``failure``. Input snapshots remain
    unchanged; post-trial hashes are new, and the frozen comparison checks the
    same reference/camera/view-set, rubric, and protected gate inventory.
    A retained result is not an independent authorization to publish an artifact.
    Pass a program-owned live ``consent`` callback when resuming on a new
    controller; it is rechecked at selection, application and outcome publication.
    """
    context, record, candidate = _selected(controller, selection, consent=consent)
    application = controller.begin_application(record, trial_revision, consent=consent)
    expected_subject = dict(context['subject'], revision=trial_revision)
    before = context['observations']['before']
    criteria = context['observations']['criteria']
    after_hashes = []
    try:
        if after_subject != expected_subject:
            raise DecisionError('stale-evidence')
        _bound_failure(controller.workspace, after_subject, after_bindings, after)
        after_hashes = _hashes(after_bindings)
    except DecisionError as exc:
        comparison = {'status': 'unevaluated', 'retained': False, 'passed': False,
                      'deliverable': False, 'reasons': [exc.reason_code]}
    else:
        comparison = compare_repair(before, after['report'], criteria)
    retained = comparison['retained']
    incumbent = trial_revision if retained else context['subject']['revision']
    outcome = {'status': 'completed' if retained else 'rejected', 'comparison': comparison,
               'retained': retained, 'deliverable': comparison['deliverable'],
               'incumbentRevision': incumbent, 'trialRevision': trial_revision,
               'hypothesis': candidate['hypothesis'], 'action': candidate['action'],
               'criteriaSha256': canonical_digest(criteria),
               'beforeBindings': _hashes(context['bindings']), 'afterBindings': after_hashes,
               'remainingTrials': max(0, context['observations']['remainingTrials'] - 1)}
    finished = controller.finish_application(application['applicationId'], outcome, consent=consent)
    return {'status': 'request-input' if comparison['status'] == 'unevaluated' else comparison['status'],
            'incumbentRevision': incumbent, 'retained': retained,
            'deliverable': comparison['deliverable'], 'comparison': comparison, 'application': finished}
