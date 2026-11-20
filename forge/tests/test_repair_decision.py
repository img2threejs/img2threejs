"""Offline consumer-visible repair contracts; no real geometry/visual claims."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

_FORGE = str(Path(__file__).resolve().parents[1])
if _FORGE not in sys.path:
    sys.path.insert(0, _FORGE)

from _shared.decision_control import DecisionController, DecisionError, canonical_digest
from stage4_review.repair_decision import begin_repair, finish_repair, prepare_repair


class RepairDecisionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name)
        self.controller = DecisionController(self.workspace, 'run-1', clock=lambda: 1000)
        self.subject = {'runId': 'run-1', 'objectId': 'object-1', 'revision': 1, 'passId': 'geometry'}
        self.common = [self.bind(role, role + '.json', {'fixture': role})
                       for role in ('reference', 'camera', 'view-set')]
        self.before = self.report('incumbent', intersections=4)
        self.failure, self.bindings = self.envelope(1, self.before)
        self.scope = {'pass_id': 'geometry', 'pass_unlocked': True, 'components': {'body'},
                      'operations': {'reshape'}, 'field_paths': {'body.radius'},
                      'authorized_sources': set(), 'source_operations': set(),
                      'available_dependencies': {'mesher'},
                      'rig_required': True, 'rig_available': True,
                      'permissions': {'write-spec', 'write-code'}, 'remainingTrials': 2,
                      'evidenceAdmitted': True, 'provenanceVerified': True, 'permissionGranted': True}
        self.criteria = {'targetMetric': 'selfIntersectionCount', 'direction': 'min', 'minImprovement': 1,
                         'tolerances': {'silhouetteIoU': 0, 'interiorDifference': 0.01, 'orbitConsistency': 0.01}}
        self.candidates = [self.candidate('reshape-a'), self.candidate('reshape-b')]
        self.grant = {'granted': True, 'purposes': ['routing', 'build-strategy', 'quality-review', 'repair'],
                      'sha256': 'd' * 64, 'tokenBudget': 10000}
        self.provider = Mock(side_effect=self.advisory)
        self.fallback = Mock(side_effect=lambda context: self.advisory(context, model='host-model'))
        self.availability = Mock(return_value={'status': 'present'})

    def bind(self, role, name, value):
        data = json.dumps(value, sort_keys=True).encode()
        (self.workspace / name).write_bytes(data)
        return {'role': role, 'path': name, 'sha256': hashlib.sha256(data).hexdigest()}

    def report(self, identifier, intersections=0):
        return {'id': identifier, 'outputKind': 'threejs', 'flatness': 'volumetric',
                'gateResults': [{'id': 'mesh', 'status': 'fail' if intersections else 'pass', 'producer': 'mesh-v1'},
                                {'id': 'rig', 'status': 'pass', 'producer': 'rig-v1'}],
                'measurements': {'width': 2, 'height': 1, 'depth': 0.4, 'frontArea': 2, 'sideArea': 0.6,
                                 'selfIntersectionCount': intersections, 'partCoveragePassed': True},
                'views': [0, 90, 180, 270],
                'features': [{'id': 'handle', 'tier': 'critical', 'status': 'pass', 'score': 3, 'threshold': 3}],
                'metrics': {'silhouetteIoU': 0.9, 'interiorDifference': 0.08, 'orbitConsistency': 0.9},
                'evidence': dict(zip(('referenceSha256', 'cameraSha256', 'viewSetSha256'),
                                     (binding['sha256'] for binding in self.common))),
                'cost': 1, 'timeMs': 10}

    def envelope(self, revision, report, faults=None):
        subject = dict(self.subject, revision=revision)
        bindings = deepcopy(self.common) + [
            self.bind('source', f'source-{revision}.json', {'revision': revision, 'fixture': 'source'}),
            self.bind('spec', f'spec-{revision}.json', {'revision': revision, 'fixture': 'spec'})]
        failure = {'subject': subject, 'bindings': deepcopy(bindings), 'report': deepcopy(report),
                   'faults': faults or []}
        bindings.append(self.bind('gate-report', f'gate-{revision}.json', failure))
        return failure, bindings

    @staticmethod
    def candidate(identifier):
        return {'id': identifier, 'hypothesis': 'geometry', 'action': 'refine-spec',
                'permissions': ['write-spec'],
                'plan': {'id': identifier, 'passId': 'geometry', 'components': ['body'],
                         'operations': ['reshape'], 'fieldPaths': ['body.radius'],
                         'materialSources': [], 'dependencies': ['mesher'], 'rigHandoff': True}}

    @staticmethod
    def advisory(context, model='jev-1.14.0', uncertain=False):
        ids = [candidate['id'] for candidate in context['candidates']]
        probabilities = {identifier: 0 for identifier in ids}
        probabilities[ids[0]] = 1
        if uncertain:
            probabilities[ids[0]], probabilities[ids[1]] = 0.6, 0.4
        return {'kind': 'img2.decision-advisory', 'version': 1, 'group': 'repair',
                'subject': deepcopy(context['subject']), 'contextDigest': canonical_digest(context), 'model': model,
                'answers': {'selection': {'type': 'choice', 'choice': ids[0], 'probabilities': probabilities,
                                          'confidence': 0.8}, 'anyFits': {'type': 'noul', 'noul': 1}},
                'usage': {'input_tokens': 10, 'output_tokens': 2}}

    def prepare(self, **overrides):
        arguments = {'subject': self.subject, 'bindings': self.bindings, 'failure': self.failure,
                     'candidates': self.candidates, 'scope': self.scope, 'criteria': self.criteria,
                     'requirements': {'criticalFeatures': [{'id': 'handle'}], 'requiredViews': [0, 90, 180, 270]},
                     'provider': self.provider, 'fallback': self.fallback, 'availability': self.availability,
                     'consent': lambda: deepcopy(self.grant), 'request_token_limit': 128}
        arguments.update(overrides)
        return prepare_repair(self.controller, **arguments)

    def finish(self, selection, report, revision=2):
        after, bindings = self.envelope(revision, report)
        return finish_repair(self.controller, selection, trial_revision=revision, after=after,
                             after_subject=dict(self.subject, revision=revision), after_bindings=bindings)

    def test_stale_source_or_failed_revision_never_calls_provider(self):
        for fault in ('changed-source', 'old-subject', 'old-input-hash', 'unbound-report'):
            with self.subTest(fault=fault):
                failure, bindings = self.envelope(1, self.before)
                if fault == 'changed-source':
                    (self.workspace / 'source-1.json').write_text('outside edit')
                elif fault == 'old-subject':
                    failure['subject']['revision'] = 0
                elif fault == 'old-input-hash':
                    failure['bindings'][3]['sha256'] = '0' * 64
                else:
                    failure['report']['measurements']['selfIntersectionCount'] = 99
                result = self.prepare(failure=failure, bindings=bindings)
                self.assertEqual(result['status'], 'request-input')
                self.assertFalse(result['deliverable'])
        self.provider.assert_not_called()
        self.fallback.assert_not_called()
        self.availability.assert_not_called()

    def test_integrity_provenance_permission_faults_stop_before_consult(self):
        for fault in ('evidence-integrity', 'stale-evidence', 'provenance', 'permission'):
            failure, bindings = self.envelope(1, self.before, faults=[fault])
            result = self.prepare(failure=failure, bindings=bindings)
            self.assertEqual(result['reasonCode'], fault)
        for key in ('evidenceAdmitted', 'provenanceVerified', 'permissionGranted'):
            scope = dict(self.scope, **{key: False})
            self.assertEqual(self.prepare(scope=scope)['status'], 'request-input')
        self.provider.assert_not_called()
        self.fallback.assert_not_called()

    def test_candidate_bypass_is_filtered_even_if_scope_lists_it(self):
        for operation, field in (('lower-threshold', 'body.radius'), ('reshape', 'gates.mesh.threshold')):
            candidate = self.candidate('bypass')
            candidate['plan'].update(operations=[operation], fieldPaths=[field])
            scope = dict(self.scope, operations={operation}, field_paths={field})
            result = self.prepare(candidates=[candidate], scope=scope)
            self.assertEqual(result['reasonCode'], 'no-admissible-repair')
            self.assertIn('forbidden-repair-action', result['rejected'][0]['reasons'])
        self.provider.assert_not_called()

    def test_scope_and_dependencies_filter_candidate_before_provider(self):
        for field, value in (('components', ['missing']), ('operations', ['exec']),
                             ('fieldPaths', ['body.hidden']), ('dependencies', ['missing']),
                             ('materialSources', ['a' * 64]), ('rigHandoff', False)):
            candidate = self.candidate('bad')
            candidate['plan'][field] = value
            self.assertEqual(self.prepare(candidates=[candidate])['status'], 'request-input')
        denied = self.candidate('denied')
        denied['permissions'] = ['upload-image']
        self.assertEqual(self.prepare(candidates=[denied])['reasonCode'], 'no-admissible-repair')
        self.assertEqual(self.prepare(scope=dict(self.scope, pass_unlocked=False))['status'], 'request-input')
        self.provider.assert_not_called()

    def test_source_operation_requires_supplied_independently_authorized_hash(self):
        candidate = self.candidate('source-repair')
        candidate['plan']['operations'] = ['fit-from-source']
        source_hash = hashlib.sha256(b'offline authorized source fixture').hexdigest()
        scope = dict(self.scope, operations={'reshape', 'fit-from-source'},
                     source_operations={'fit-from-source'}, authorized_sources={source_hash})
        missing = self.prepare(candidates=[candidate], scope=scope)
        self.assertEqual(missing['reasonCode'], 'no-admissible-repair')
        self.assertIn('material-source-required', missing['rejected'][0]['reasons'])
        candidate['plan']['materialSources'] = ['0' * 64]
        unauthorized = self.prepare(candidates=[candidate], scope=scope)
        self.assertEqual(unauthorized['reasonCode'], 'no-admissible-repair')
        self.assertIn('unauthorized-material-source', unauthorized['rejected'][0]['reasons'])
        self.provider.assert_not_called()
        self.fallback.assert_not_called()
        self.availability.assert_not_called()

    def test_source_operation_scope_is_frozen_with_authorized_candidate(self):
        candidate = self.candidate('source-repair')
        source_hash = hashlib.sha256(b'offline authorized source fixture').hexdigest()
        candidate['plan'].update(operations=['fit-from-source'], materialSources=[source_hash])
        scope = dict(self.scope, operations={'reshape', 'fit-from-source'},
                     source_operations={'fit-from-source'}, authorized_sources={source_hash})
        selected = self.prepare(candidates=[candidate], scope=scope)
        self.assertEqual(selected['status'], 'refine-spec')
        self.assertEqual(selected['context']['requirements']['repairScope']['source_operations'], ['fit-from-source'])
        self.assertEqual(self.provider.call_args.args[0]['candidates'][0]['plan']['materialSources'], [source_hash])

    def test_action_cannot_omit_its_intrinsic_permission(self):
        candidate = self.candidate('missing-permission')
        candidate['permissions'] = []
        self.assertEqual(self.prepare(candidates=[candidate])['reasonCode'], 'no-admissible-repair')
        candidate['action'] = 'refine-code'
        candidate['permissions'] = ['write-code']
        scope = dict(self.scope, permissions={'write-spec'})
        self.assertEqual(self.prepare(candidates=[candidate], scope=scope)['reasonCode'], 'no-admissible-repair')
        self.provider.assert_not_called()

    def test_malformed_plan_collection_is_rejected_before_provider(self):
        candidate = self.candidate('malformed')
        candidate['plan']['operations'] = None
        self.assertEqual(self.prepare(candidates=[candidate])['reasonCode'], 'no-admissible-repair')
        self.provider.assert_not_called()

    def test_geometry_failure_is_diagnosable_and_does_not_mutate_files(self):
        original = {binding['path']: (self.workspace / binding['path']).read_bytes() for binding in self.bindings}
        result = self.prepare()
        self.assertEqual(result['status'], 'refine-spec')
        self.assertEqual(self.provider.call_args.args[0]['observations']['before']['measurements']['selfIntersectionCount'], 4)
        self.assertFalse(result['deliverable'])
        self.assertEqual(original, {name: (self.workspace / name).read_bytes() for name in original})
        self.assertTrue(all('path' not in binding for binding in self.provider.call_args.args[0]['bindings']))

    def test_missing_or_unmeasured_criteria_blocks_before_provider(self):
        for criteria in (None, {}, dict(self.criteria, targetMetric='confidence'),
                         dict(self.criteria, tolerances={})):
            self.assertEqual(self.prepare(criteria=criteria)['status'], 'request-input')
        self.provider.assert_not_called()

    def test_same_successor_resumes_once_and_second_successor_is_forbidden(self):
        selected = self.prepare()
        first = begin_repair(self.controller, selected, 2)
        reopened = DecisionController(self.workspace, 'run-1', clock=lambda: 1000)
        self.controller = reopened
        resumed = self.prepare()
        self.assertEqual(begin_repair(reopened, resumed, 2), first)
        with self.assertRaises(DecisionError) as caught:
            begin_repair(reopened, resumed, 3)
        self.assertEqual(caught.exception.reason_code, 'application-conflict')
        self.assertEqual(self.provider.call_count, 1)
        result = self.finish(resumed, self.report('trial'))
        self.assertEqual(result['status'], 'retain')
        self.assertEqual(begin_repair(reopened, resumed, 2), result['application'])
        self.assertEqual(self.finish(resumed, self.report('trial')), result)

    def test_fresh_controller_resumes_recorded_trial_with_explicit_live_consent(self):
        selected = self.prepare()
        pending = begin_repair(self.controller, selected, 2)
        reopened = DecisionController(self.workspace, 'run-1', clock=lambda: 1000)
        consent = lambda: deepcopy(self.grant)
        self.assertEqual(begin_repair(reopened, selected, 2, consent=consent), pending)
        with self.assertRaises(DecisionError) as caught:
            begin_repair(reopened, selected, 3, consent=consent)
        self.assertEqual(caught.exception.reason_code, 'application-conflict')
        after, bindings = self.envelope(2, self.report('trial'))
        arguments = {'trial_revision': 2, 'after': after, 'after_subject': dict(self.subject, revision=2),
                     'after_bindings': bindings, 'consent': consent}
        result = finish_repair(reopened, selected, **arguments)
        fresh = DecisionController(self.workspace, 'run-1', clock=lambda: 1000)
        self.assertEqual(begin_repair(fresh, selected, 2, consent=consent), result['application'])
        self.assertEqual(finish_repair(fresh, selected, **arguments), result)
        self.assertEqual(result['status'], 'retain')
        self.assertEqual(self.provider.call_count, 1)
        self.fallback.assert_not_called()

    def test_fresh_controller_missing_or_revoked_consent_blocks_resume_and_finish(self):
        selected = self.prepare()
        begin_repair(self.controller, selected, 2)
        reopened = DecisionController(self.workspace, 'run-1', clock=lambda: 1000)
        with self.assertRaises(DecisionError) as caught:
            begin_repair(reopened, selected, 2)
        self.assertEqual(caught.exception.reason_code, 'consent-unproven')
        self.grant['granted'] = False
        consent = lambda: deepcopy(self.grant)
        with self.assertRaises(DecisionError) as caught:
            begin_repair(reopened, selected, 2, consent=consent)
        self.assertEqual(caught.exception.reason_code, 'consent-changed')
        after, bindings = self.envelope(2, self.report('trial'))
        with self.assertRaises(DecisionError) as caught:
            finish_repair(reopened, selected, trial_revision=2, after=after,
                          after_subject=dict(self.subject, revision=2), after_bindings=bindings, consent=consent)
        self.assertEqual(caught.exception.reason_code, 'consent-changed')
        self.assertEqual(self.provider.call_count, 1)
        self.fallback.assert_not_called()

    def test_live_consent_is_rechecked_before_publishing_retention(self):
        selected = self.prepare()
        pending = begin_repair(self.controller, selected, 2)
        reopened = DecisionController(self.workspace, 'run-1', clock=lambda: 1000)
        revoked = dict(self.grant, granted=False)
        consent = Mock(side_effect=[deepcopy(self.grant), deepcopy(self.grant), revoked])
        after, bindings = self.envelope(2, self.report('trial'))
        arguments = {'trial_revision': 2, 'after': after, 'after_subject': dict(self.subject, revision=2),
                     'after_bindings': bindings}
        with self.assertRaises(DecisionError) as caught:
            finish_repair(reopened, selected, consent=consent, **arguments)
        self.assertEqual(caught.exception.reason_code, 'consent-changed')
        self.assertEqual(consent.call_count, 3)
        self.assertEqual(begin_repair(reopened, selected, 2, consent=lambda: deepcopy(self.grant)), pending)
        self.assertEqual(self.provider.call_count, 1)

    def test_outside_edit_invalidates_application_without_overwriting_incumbent(self):
        selected = self.prepare()
        begin_repair(self.controller, selected, 2)
        (self.workspace / 'source-1.json').write_text('outside edit')
        with self.assertRaises(DecisionError) as caught:
            begin_repair(self.controller, selected, 2)
        self.assertEqual(caught.exception.reason_code, 'stale-evidence')
        self.assertEqual(self.before['measurements']['selfIntersectionCount'], 4)

    def test_criterion_and_selected_plan_cannot_be_replaced_after_choice(self):
        selected = self.prepare()
        for alteration in ('criteria', 'plan'):
            tampered = deepcopy(selected)
            if alteration == 'criteria':
                tampered['context']['observations']['criteria']['minImprovement'] = 0
            else:
                tampered['context']['candidates'][0]['plan']['operations'] = ['disable-gate']
            with self.assertRaises(DecisionError):
                begin_repair(self.controller, tampered, 2)
        self.criteria['minImprovement'] = 999
        self.assertEqual(self.finish(selected, self.report('trial'))['status'], 'retain')

    def test_target_improvement_with_rig_regression_retains_incumbent(self):
        selected = self.prepare()
        report = self.report('trial')
        report['gateResults'][1]['status'] = 'fail'
        result = self.finish(selected, report)
        self.assertEqual(result['status'], 'reject')
        self.assertEqual(result['incumbentRevision'], 1)
        self.assertFalse(result['retained'])
        self.assertFalse(result['deliverable'])
        self.assertIn('regression:gate:rig', result['comparison']['reasons'])

    def test_partial_target_improvement_is_not_deliverable(self):
        result = self.finish(self.prepare(), self.report('trial', intersections=2))
        self.assertEqual(result['status'], 'retain')
        self.assertEqual(result['incumbentRevision'], 2)
        self.assertFalse(result['deliverable'])
        self.assertEqual(result['comparison']['improvement'], 2)

    def test_confident_selection_without_improvement_rejects_trial(self):
        result = self.finish(self.prepare(), self.report('trial', intersections=4))
        self.assertEqual(result['status'], 'reject')
        self.assertEqual(result['incumbentRevision'], 1)
        self.assertFalse(result['deliverable'])

    def test_post_trial_evidence_requires_successor_and_matched_setup(self):
        selected = self.prepare()
        after, bindings = self.envelope(2, self.report('trial'))
        wrong_subject = dict(self.subject, revision=3)
        result = finish_repair(self.controller, selected, trial_revision=2, after=after,
                               after_subject=wrong_subject, after_bindings=bindings)
        self.assertEqual(result['status'], 'request-input')
        self.assertEqual(result['incumbentRevision'], 1)
        self.assertFalse(result['deliverable'])

    def test_changed_camera_is_incomparable_not_success(self):
        selected = self.prepare()
        self.common[1] = self.bind('camera', 'new-camera.json', {'fixture': 'changed camera'})
        result = self.finish(selected, self.report('trial'))
        self.assertEqual(result['status'], 'request-input')
        self.assertEqual(result['incumbentRevision'], 1)
        self.assertFalse(result['retained'])

    def test_uncertain_jev_requests_input_without_model_override_or_trial(self):
        self.provider.side_effect = lambda context: self.advisory(context, uncertain=True)
        selected = self.prepare()
        self.assertEqual(selected['status'], 'request-input')
        self.assertEqual(selected['record']['status'], 'uncertain')
        with self.assertRaises(DecisionError):
            begin_repair(self.controller, selected, 2)
        self.fallback.assert_not_called()

    def test_unavailable_group_uses_shared_fallback_with_same_retention_gate(self):
        selected = self.prepare(availability=lambda: {'status': 'capability-unavailable'})
        self.assertEqual(selected['record']['unavailableReason'], 'capability-unavailable')
        self.assertEqual(self.finish(selected, self.report('trial', intersections=4))['status'], 'reject')
        self.provider.assert_not_called()
        self.fallback.assert_called_once()

    def test_login_suspends_and_exhausted_budget_never_authorizes_trial(self):
        selected = self.prepare(availability=lambda: {'status': 'credential-missing'})
        self.assertEqual(selected['record']['phase'], 'suspended')
        self.assertEqual(selected['record']['loginCommand'], 'img2-jev login')
        self.assertEqual(self.prepare(scope=dict(self.scope, remainingTrials=0))['status'], 'stop')
        self.provider.assert_not_called()
        self.fallback.assert_not_called()


if __name__ == '__main__':
    unittest.main()
