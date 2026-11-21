"""Negative placement boundaries; no provider calls, renderer mocks or quality claims."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

_FORGE = str(Path(__file__).resolve().parents[1])
if _FORGE not in sys.path:
    sys.path.insert(0, _FORGE)

from _shared.decision_control import DecisionController, DecisionError, GROUPS, canonical_digest
from _shared.placement_decision import consume_placement, placement_context
from stage6_compose.compose import CompositionError
from stage6_compose.measure import _bound
from stage6_compose.placement import _position, _source_rows, produce_placement, solve_camera, validate_metric_anchor


class PlacementProducerNegatives(unittest.TestCase):
    def test_later_outage_does_not_invalidate_earlier_real_journal_jev_answer(self):
        from forge.tests.test_scene_composer import SceneComposerTests
        fixture = SceneComposerTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        root = fixture.root
        row = fixture.placement['objects'][0]
        refs = [{'role':role, **ref} for role, ref in (
            ('inventory',fixture.request['inventory']), ('reference',fixture.request['reference']),
            ('artifact',fixture.request['artifacts'][0]), ('camera',fixture.placement['camera']),
            ('size-prior',row['sizePrior']), ('depth',row['evidence']['depth']), ('support',row['evidence']['support']))]
        subject = {'runId':'run','objectId':row['objectId'],'revision':1,'passId':'unused'}
        contexts = {question:placement_context(subject, refs, question=question,
            options=[{'id':'selected','value':value}]) for question, value in (('size',row['scale']),('yaw',row['yawBin']))}
        controller = DecisionController(root, 'run')
        grant = {'granted':True,'purposes':list(GROUPS),'sha256':'a'*64,'tokenBudget':10000}
        def consent():
            return dict(grant)
        def advisory(context, model):
            return {'kind':'img2.decision-advisory','version':1,'group':context['group'],
                    'subject':context['subject'],'contextDigest':canonical_digest(context),'model':model,
                    'answers':{'selection':{'type':'choice','choice':'selected','confidence':1,
                                           'probabilities':{'selected':1}},
                               'anyFits':{'type':'noul','noul':1}},
                    'usage':{'input_tokens':10,'output_tokens':2}}
        records = {}
        records['size'] = controller.consult(contexts['size'],
            provider=lambda context:advisory(context,'jev-1.14.0'), availability=lambda:{'status':'present'},
            consent=consent, request_token_limit=128)
        records['yaw'] = controller.consult(contexts['yaw'],
            fallback=lambda context:advisory(context,'offline-host-fixture'),
            availability=lambda:{'status':'not-installed'}, consent=consent, request_token_limit=128)
        with controller.workspace_transaction(consent=consent) as verify:
            for question in ('size','yaw'):
                answer = consume_placement(verify, contexts[question], records[question])
                context_ref = fixture.put('journal-' + question + '-context.json', contexts[question])
                record_ref = fixture.put('journal-' + question + '-record.json', records[question])
                answer['decisionEvidence'] = {'context':context_ref,'record':record_ref}
                answer['sessionAtDecision'] = 'healthy' if question == 'size' else 'unavailable:not-installed'
                if question == 'yaw':
                    answer['unavailableReceipt'] = record_ref
                row[question+'Answer'] = fixture.put('verified-' + question + '.json', answer)
            fixture.placement['jevSession'] = 'unavailable:not-installed'
            fixture.save_placement()
            result = fixture.compose()
        self.assertEqual(records['size']['decider'], 'jev:jev-1.14.0')
        self.assertTrue(records['yaw']['decider'].startswith('model:'))
        self.assertFalse(result['deliverable'])

    def test_json_decider_is_not_a_placement_authority(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(CompositionError, 'placement-authority-required'):
                produce_placement(root, preparation={'decider':'jev:claimed','value':[1,1,1]},
                                  decisions={}, verify_answer=None, output_dir='placement')
            self.assertFalse(Path(root, 'placement').exists())

    def test_saved_camera_scores_cannot_replace_landmark_solve(self):
        with self.assertRaisesRegex(CompositionError, 'missing-camera-correspondences'):
            solve_camera({'fit':{'convergence':{'status':'converged'},'finalReprojectionError':0},
                          'camera':{'position':[0,1,5]}})

    def test_insufficient_landmarks_fail_before_any_browser_run(self):
        record = {'initialCamera': {'imageWidth':64,'imageHeight':64,'fovDegrees':45,
            'yawDegrees':0,'pitchDegrees':0,'rollDegrees':0,'position':[0,1,5]},
            'correspondences':[{'name':'one','world':[0,0,0],'observed':[32,32]}], 'near':0.1,'far':100}
        with self.assertRaisesRegex(CompositionError, 'invalid-camera-correspondences'):
            solve_camera(record)

    def test_unanchored_camera_world_scale_is_not_metric_evidence(self):
        with self.assertRaisesRegex(CompositionError, 'missing-camera-metric-anchor'):
            validate_metric_anchor({'correspondences':[]}, [], {})

    def test_arbitrary_class_height_cannot_calibrate_depth(self):
        points = [[0,0,0], [0,1,0]]
        raw = {'correspondences':[{'world':point} for point in points],
               'metricAnchor':{'mode':'size-prior','classId':'mug','heightMeters':1,'worldPoints':points}}
        with self.assertRaisesRegex(CompositionError, 'scale-outside-prior'):
            validate_metric_anchor(raw, [{'objectId':'mug','artifactObjectId':'mug','classId':'mug','role':'prop'}],
                                   {'mug':{'artifact':{}}})

    def test_missing_embedded_support_is_not_inherited_from_container_free_mode(self):
        with self.assertRaisesRegex(CompositionError, 'inventory-mismatch'):
            _source_rows({'objects':[{'objectId':'room','mode':'free'}]}, {'room','embedded-chair'}, 'support')

    def test_duplicate_depth_rows_cannot_replace_missing_instance(self):
        with self.assertRaisesRegex(CompositionError, 'inventory-mismatch'):
            _source_rows({'objects':[{'objectId':'chair','relativeDepth':1}]*2}, {'chair','chair-2'}, 'depth')

    def test_stale_metric_anchor_binding_does_not_become_passing_evidence(self):
        with self.assertRaisesRegex(CompositionError, 'stale-placement-evidence'):
            _bound({'referenceSha256':'a'*64}, {'referenceSha256':'b'*64}, 'depth')

    def test_empty_reference_box_cannot_generate_plausible_position(self):
        camera = {'position':[0,0,5],'target':[0,0,4],'up':[0,1,0],'fov':45,'aspect':1}
        with self.assertRaisesRegex(CompositionError, 'invalid-projected-box'):
            _position(camera, [0.5,0.5,0.5,0.5], 5)

    def test_forged_healthy_jev_answer_not_in_base_journal_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            refs = []
            for role in ('inventory','reference','artifact','camera','size-prior','depth','support'):
                payload = json.dumps({'role':role}).encode()
                path = role + '.json'
                (root/path).write_bytes(payload)
                refs.append({'role':role,'path':path,'sha256':hashlib.sha256(payload).hexdigest()})
            subject = {'runId':'run','objectId':'chair','revision':1,'passId':'unused'}
            context = placement_context(subject, refs, question='yaw', options=[{'id':'yaw-0','value':0}])
            forged = {'kind':'img2.decision-record','version':1,'recordId':'0'*64,
                      'decider':'jev:claimed','status':'answered','reasonCode':'answered',
                      'subject':context['subject'],'group':'build-strategy',
                      'answers':{'selection':{'choice':'yaw-0'},'anyFits':{'noul':1}}}
            controller = DecisionController(root, 'run')
            grant = {'granted':True,'purposes':['build-strategy'],'sha256':'a'*64,'tokenBudget':1000}
            with controller.workspace_transaction(consent=lambda: grant) as verify:
                with self.assertRaisesRegex(DecisionError, 'record-mismatch'):
                    consume_placement(verify, context, forged)
            self.assertFalse((root/'placement.json').exists())


if __name__ == '__main__':
    unittest.main()
