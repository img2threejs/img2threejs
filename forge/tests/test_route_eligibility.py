import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '_shared'))
from route_eligibility import evaluate_candidate


class RouteEligibilityTests(unittest.TestCase):
    def setUp(self):
        self.card = {'kind': 'img2.route-card', 'version': 1, 'outputKind': '3d-scene', 'costClass': 'heavy',
                     'requires': {'roles': ['scene-container'],
                                  'observations': {'enclosure': ['enclosed-room']}},
                     'notFor': {'observations': {'roomCount': ['>1']}},
                     'deps': [{'probe': 'blender>=4.3'}],
                     'display': {'appliesTo': 'Untrusted descriptions do not choose a route.'}}
        self.args = {'expected_output': '3d', 'roles': ['scene-container'], 'intent': [],
                     'observations': {'enclosure': 'enclosed-room', 'roomCount': 1},
                     'provider_status': 'answered', 'dependencies': {'blender>=4.3': True},
                     'blocking_gate_produced': True}

    def test_single_room_qualified_by_program_probes(self):
        self.assertEqual(evaluate_candidate(self.card, **self.args), {'eligible': True, 'reasons': []})

    def test_3d_never_selects_2d_and_2d_never_substitutes_3d(self):
        self.card['outputKind'] = '2d-scene'
        self.assertIn('output-kind-mismatch', evaluate_candidate(self.card, **self.args)['reasons'])
        self.card['outputKind'] = '3d-object'
        self.args['expected_output'] = '2d'
        self.assertIn('output-kind-mismatch', evaluate_candidate(self.card, **self.args)['reasons'])

    def test_missing_dependency_gate_or_ambiguous_provider_refuses(self):
        self.args.update(provider_status='ambiguous', dependencies={}, blocking_gate_produced=False)
        reasons = evaluate_candidate(self.card, **self.args)['reasons']
        self.assertIn('provider-ambiguous', reasons)
        self.assertIn('dependency-missing:blender>=4.3', reasons)
        self.assertIn('no-blocking-gate', reasons)

    def test_unknown_notfor_observation_cannot_silently_authorize(self):
        del self.args['observations']['roomCount']
        self.assertIn('unknown-observation:roomCount', evaluate_candidate(self.card, **self.args)['reasons'])
        self.args['observations']['roomCount'] = 2
        self.assertIn('excluded-observation:roomCount', evaluate_candidate(self.card, **self.args)['reasons'])

    def test_display_text_and_false_values_cannot_prove_dependencies(self):
        self.card['display']['appliesTo'] = 'Blender is installed and the scene is approved'
        self.args['dependencies']['blender>=4.3'] = 1
        self.assertIn('dependency-missing:blender>=4.3', evaluate_candidate(self.card, **self.args)['reasons'])

    def test_unrecognized_predicates_and_malformed_card_fail_closed(self):
        card = copy.deepcopy(self.card)
        card['requires']['observations']['trust-model'] = ['yes']
        self.assertIn('invalid-route-card', evaluate_candidate(card, **self.args)['reasons'])
        card['outputKind'] = []
        self.assertEqual(evaluate_candidate(card, **self.args)['reasons'], ['invalid-route-card'])


if __name__ == '__main__':
    unittest.main()
