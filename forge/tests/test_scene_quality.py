import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '_shared'))
from scene_quality import assess_scene as assess_measured_scene

SCENE = 'a' * 64
REFERENCE = 'b' * 64


def measurement(value):
    return {'value': value, 'producer': 'measured-fixture', 'sceneSha256': SCENE,
            'referenceSha256': REFERENCE}

def assess_scene(scene, policy=None):
    return assess_measured_scene(scene, expected_object_ids=['room', 'prop'], policy=policy)


def fixture():
    def obj(name):
        return {'id': name, 'supported': True, 'boxIoU': measurement(0.8),
                'height': measurement(1), 'contactGap': measurement(0.005),
                'scale': measurement(1), 'scalePrior': {'min': 0.5, 'max': 2}}
    return {'sceneSha256': SCENE, 'referenceSha256': REFERENCE,
            'objects': [obj('room'), obj('prop')],
            'intersections': [{'objects': ['room', 'prop'], 'count': measurement(0)}],
            'silhouetteIoU': measurement(0.8), 'interiorDifference': measurement(0.1),
            'lightRigCount': measurement(1)}


class SceneQualityTests(unittest.TestCase):
    def test_valid_measured_scene_passes(self):
        report = assess_scene(fixture())
        self.assertEqual(report['status'], 'pass')
        self.assertTrue(report['deliverable'])

    def test_floating_prop_and_wrong_scale_block_delivery(self):
        scene = fixture()
        scene['objects'][1]['contactGap'] = measurement(0.05)
        scene['objects'][1]['scale'] = measurement(3)
        report = assess_scene(scene)
        self.assertEqual(report['status'], 'fail')
        self.assertFalse(report['deliverable'])
        self.assertEqual({c['id'] for c in report['checks'] if c['status'] == 'fail'},
                         {'object:prop:contact', 'object:prop:scale'})

    def test_missing_or_stale_pair_is_not_a_pass(self):
        for change in ('missing', 'stale', 'duplicate'):
            with self.subTest(change=change):
                scene = fixture()
                if change == 'missing':
                    scene['intersections'] = []
                elif change == 'stale':
                    scene['intersections'][0]['count']['sceneSha256'] = 'c' * 64
                else:
                    scene['intersections'].append(copy.deepcopy(scene['intersections'][0]))
                report = assess_scene(scene)
                self.assertEqual(report['status'], 'unevaluated')
                self.assertFalse(report['deliverable'])

    def test_intersection_and_interior_mismatch_are_failures(self):
        scene = fixture()
        scene['intersections'][0]['count'] = measurement(1)
        scene['interiorDifference'] = measurement(0.2)
        report = assess_scene(scene)
        self.assertEqual(report['status'], 'fail')
        self.assertEqual({c['id'] for c in report['checks'] if c['status'] == 'fail'},
                         {'intersection:prop:room', 'scene:interior-difference'})

    def test_unsupported_object_does_not_require_contact_measurement(self):
        scene = fixture()
        scene['objects'][1]['supported'] = False
        del scene['objects'][1]['contactGap']
        self.assertEqual(assess_scene(scene)['status'], 'pass')

    def test_missing_inventory_nonfinite_measurement_and_bad_light_count_block(self):
        scene = fixture()
        scene['objects'] = []
        self.assertEqual(assess_scene(scene)['status'], 'unevaluated')
        scene = fixture()
        scene['silhouetteIoU'] = measurement(float('nan'))
        self.assertEqual(assess_scene(scene)['status'], 'unevaluated')
        scene = fixture()
        scene['lightRigCount'] = measurement(2)
        self.assertEqual(assess_scene(scene)['status'], 'fail')

    def test_missing_routed_object_never_passes(self):
        scene = fixture()
        scene['objects'].pop()
        scene['intersections'] = []
        report = assess_scene(scene)
        self.assertEqual(report['status'], 'unevaluated')
        self.assertIn('scene-does-not-match-routed-inventory',
                      [check['reason'] for check in report['checks']])


    def test_policy_cannot_be_weakened_by_caller(self):
        weakened = {'version': 'scene-quality-policy-v1',
                    'scene': {'minimumObjectBoxIoU': 0, 'maximumContactGapHeightRatio': 1,
                              'minimumSilhouetteIoU': 0, 'maximumInteriorDifference': 1}}
        report = assess_scene(fixture(), policy=weakened)
        self.assertEqual(report['status'], 'unevaluated')
        self.assertFalse(report['deliverable'])

if __name__ == '__main__':
    unittest.main()
