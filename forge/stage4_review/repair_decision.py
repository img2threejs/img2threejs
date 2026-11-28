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
import math
from pathlib import Path, PurePosixPath

from _shared.decision_control import DecisionError, canonical_digest
from _shared.decision_quality import assess_candidate, compare_repair
from _shared.plan_eligibility import filter_plans
from stage4_review.quality_decision import _prepare, _read_bindings
from stage2_spec.strategy_decision import _read_binding

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


def _capture_configuration(controller, context, documents):
    """Semantic comparisons require explicit, closed physical capture metadata.

    The camera binding uses img2.repair-camera v1. Its per-view matrices,
    framing rectangle, complete pose transforms, time and dimensions plus the
    renderer configuration are compared verbatim, not normalized heuristically.
    This is evidence, not provenance authority: the host must independently
    admit these bytes against the actual capture program/configuration.
    """
    def require(condition):
        if not condition:
            raise DecisionError('semantic-capture-incomparable')
    def number(value):
        return type(value) in (int, float) and math.isfinite(value)
    def vector(value, length):
        return type(value) is list and len(value) == length and all(number(item) for item in value)
    camera_binding = next(binding for binding in context['bindings'] if binding['role'] == 'camera')
    try:
        camera = json.loads(_read_binding(controller.workspace, camera_binding))
    except (ValueError, UnicodeError):
        raise DecisionError('semantic-capture-incomparable') from None
    require(type(camera) is dict and set(camera) == {'kind', 'version', 'subject', 'views', 'renderer'}
            and camera['kind'] == 'img2.repair-camera' and type(camera['version']) is int and camera['version'] == 1
            and camera['subject'] == context['subject'])
    views = documents['view-set']
    require(set(views) == {'subject', 'inputs', 'views', 'features'})
    require(type(camera['views']) is list
            and [item.get('id') for item in camera['views'] if type(item) is dict]
            == [item['id'] for item in views['views']])
    for view in camera['views']:
        require(type(view) is dict and set(view) == {
            'id', 'matrixWorld', 'projectionMatrix', 'framing', 'pose', 'timeMs', 'width', 'height'})
        require(type(view['id']) is int and 0 <= view['id'] <= 360)
        require(vector(view['matrixWorld'], 16) and vector(view['projectionMatrix'], 16)
                and vector(view['framing'], 4) and view['framing'][2] > 0 and view['framing'][3] > 0
                and number(view['timeMs']) and view['timeMs'] >= 0
                and all(type(view[key]) is int and view[key] > 0 for key in ('width', 'height')))
        pose = view['pose']
        require(type(pose) is dict and set(pose) == {'id', 'transforms'}
                and type(pose['id']) is str and bool(pose['id']) and type(pose['transforms']) is list)
        seen = set()
        for transform in pose['transforms']:
            require(type(transform) is dict and set(transform) == {'id', 'matrixWorld'}
                    and type(transform['id']) is str and bool(transform['id'])
                    and transform['id'] not in seen and vector(transform['matrixWorld'], 16))
            seen.add(transform['id'])
    render = camera['renderer']
    require(type(render) is dict and set(render) == {
        'engine', 'version', 'width', 'height', 'samples', 'exposure', 'worldColor', 'worldStrength', 'lights'})
    require(all(type(render[key]) is str and bool(render[key]) for key in ('engine', 'version'))
            and all(type(render[key]) is int and render[key] > 0 for key in ('width', 'height', 'samples'))
            and number(render['exposure']) and vector(render['worldColor'], 3)
            and number(render['worldStrength']) and render['worldStrength'] >= 0
            and type(render['lights']) is list
            and render['width'] * render['height'] <= 16_000_000 and render['samples'] <= 256
            and all(view['width'] == render['width'] and view['height'] == render['height']
                    for view in camera['views']))
    for light in render['lights']:
        require(type(light) is dict and set(light) == {'position', 'target', 'energy', 'size', 'color'}
                and all(vector(light[key], 3) for key in ('position', 'target', 'color'))
                and light['position'] != light['target']
                and all(number(light[key]) and light[key] > 0 for key in ('energy', 'size')))
    return {'views': camera['views'], 'renderer': render,
            'viewRoles': [{'id': item['id'], 'role': item['role']} for item in views['views']],
            'features': [{key: item[key] for key in ('id', 'referenceRole', 'referenceSha256', 'renderRole')}
                         for item in views['features']]}


def _quality_evidence(controller, review, subject, bindings, report, semantic, *, consent=None, verify=None):
    if (type(review) is not dict or set(review) != {'context', 'record'}
            or type(semantic) is not dict):
        raise DecisionError('semantic-quality-review-required')
    context, record = review['context'], review['record']
    if context.get('group') != 'quality-review' or context.get('subject') != subject:
        raise DecisionError('subject-mismatch')
    # The controller authenticates the record; JSON-shaped scores alone never
    # supply semantic repair authority, including on a resumed process.
    try:
        current = verify(record, context) if verify else controller.consume(record, context, consent=consent)
    except ValueError as exc:
        if hasattr(exc, 'reason_code'):
            raise DecisionError(exc.reason_code) from None
        raise
    if current['status'] != 'answered':
        raise DecisionError('semantic-quality-review-required')
    assessment = _prepare(controller, context)
    hashes, documents = _read_bindings(controller, context)
    envelope_hashes = {item['role']: item['sha256'] for item in bindings}
    if (any(hashes[role] != envelope_hashes.get(role)
            for role in ('reference', 'spec', 'source', 'camera', 'view-set'))
            or canonical_digest(assessment['report']) != canonical_digest(report)):
        raise DecisionError('evidence-mismatch')
    scores, thresholds = {}, {}
    requirements = context['requirements']
    for dimension in requirements['dimensions']:
        item = {'id': dimension} if type(dimension) is str else dimension
        key = 'dimension:' + item['id']
        answer = current['answers'].get(key, {})
        if answer.get('type') != 'score':
            raise DecisionError('semantic-quality-unevaluated')
        scores[key], thresholds[key] = answer['score'], item.get('threshold', 3)
    if requirements['criticalFeatures'] and semantic.get('choiceScores') != {'pass': 4, 'fail': 0}:
        raise DecisionError('semantic-choice-map-required')
    for feature in requirements['criticalFeatures']:
        key = 'critical:' + feature['id']
        answer = current['answers'].get(key, {})
        if answer.get('type') != 'choice' or answer.get('choice') not in {'pass', 'fail'}:
            raise DecisionError('semantic-quality-unevaluated')
        scores[key], thresholds[key] = semantic['choiceScores'][answer['choice']], 4
    capture = _capture_configuration(controller, context, documents)
    return {'scores': scores, 'thresholds': thresholds,
            'captureDigest': canonical_digest(capture), 'rubricDigest': canonical_digest(requirements)}, {
        'contextDigest': canonical_digest(context), 'recordId': current['recordId'],
        'recordDigest': canonical_digest(current)}, capture


def _semantic_pair(controller, before_review, after_review, context, after_subject, after_bindings,
                   after_report, *, consent=None, verify=None):
    criteria = context['observations']['criteria']
    first, first_proof, first_capture = _quality_evidence(controller, before_review, context['subject'],
        context['bindings'], context['observations']['before'], criteria['semantic'], consent=consent, verify=verify)
    second, second_proof, second_capture = _quality_evidence(controller, after_review, after_subject,
        after_bindings, after_report, criteria['semantic'], consent=consent, verify=verify)
    if (first_proof != context['observations'].get('qualityReview')
            or canonical_digest(before_review['context']['requirements'])
            != canonical_digest(after_review['context']['requirements'])
            or before_review['context']['questionSetVersion'] != after_review['context']['questionSetVersion']
            or before_review['context']['policyVersion'] != after_review['context']['policyVersion']
            or first_capture != second_capture):
        raise DecisionError('semantic-capture-or-rubric-incomparable')
    comparison = compare_repair(context['observations']['before'], after_report, criteria,
                               semantic_before=first, semantic_after=second)
    return comparison, {'before': first_proof, 'after': second_proof}


def prepare_repair(controller, *, subject, bindings, failure, candidates, scope, criteria,
                   requirements, policy_version='repair-policy-v1', requested_model='jev-1.14.0',
                   provider=None, fallback=None, availability=None, consent=None,
                   request_token_limit=64000, quality_review=None):
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
    semantic_scores, quality_proof = None, None
    if assess_candidate(before)['status'] != 'fail':
        if quality_review is None:
            return _blocked('fresh-failed-gate-required')
        try:
            semantic_scores, quality_proof, unused_capture = _quality_evidence(controller, quality_review,
                subject, bindings, before, criteria.get('semantic'), consent=consent)
        except DecisionError as exc:
            return _blocked(exc.reason_code)
        if not any(semantic_scores['scores'][key] < threshold
                   for key, threshold in semantic_scores['thresholds'].items()):
            return _blocked('fresh-failed-quality-required')
    # Validate frozen measured/semantic criteria without fabricating a failed
    # gate or a measurement merely to make a semantic repair selectable.
    if compare_repair(before, before, criteria, semantic_before=semantic_scores,
                      semantic_after=semantic_scores)['status'] == 'unevaluated':
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
    if quality_proof is not None:
        context['observations']['qualityReview'] = quality_proof
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
    if quality_proof is not None:
        result['qualityReview'] = deepcopy(quality_review)
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
    if 'qualityReview' in context['observations']:
        unused_scores, proof, unused_capture = _quality_evidence(controller, selection.get('qualityReview'),
            context['subject'], context['bindings'], context['observations']['before'],
            context['observations']['criteria']['semantic'], consent=consent)
        if proof != context['observations']['qualityReview']:
            raise DecisionError('semantic-quality-review-required')
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
                  consent=None, after_quality_review=None):
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
    quality_proofs = None
    try:
        if after_subject != expected_subject:
            raise DecisionError('stale-evidence')
        _bound_failure(controller.workspace, after_subject, after_bindings, after)
        after_hashes = _hashes(after_bindings)
        if 'qualityReview' in context['observations']:
            comparison, quality_proofs = _semantic_pair(controller, selection.get('qualityReview'),
                after_quality_review, context, after_subject, after_bindings, after['report'], consent=consent)
        else:
            comparison = compare_repair(before, after['report'], criteria)
    except DecisionError as exc:
        comparison = {'status': 'unevaluated', 'retained': False, 'passed': False,
                      'deliverable': False, 'reasons': [exc.reason_code]}
    retained = comparison['retained']
    incumbent = trial_revision if retained else context['subject']['revision']
    outcome = {'status': 'completed' if retained else 'rejected', 'comparison': comparison,
               'retained': retained, 'deliverable': comparison['deliverable'],
               'incumbentRevision': incumbent, 'trialRevision': trial_revision,
               'hypothesis': candidate['hypothesis'], 'action': candidate['action'],
               'criteriaSha256': canonical_digest(criteria),
               'beforeBindings': _hashes(context['bindings']), 'afterBindings': after_hashes,
               'remainingTrials': max(0, context['observations']['remainingTrials'] - 1)}
    if quality_proofs is not None:
        outcome['qualityReviews'] = quality_proofs
    finished = controller.finish_application(application['applicationId'], outcome, consent=consent)
    return {'status': 'request-input' if comparison['status'] == 'unevaluated' else comparison['status'],
            'incumbentRevision': incumbent, 'retained': retained,
            'deliverable': comparison['deliverable'], 'comparison': comparison, 'application': finished}
