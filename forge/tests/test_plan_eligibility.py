import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '_shared'))
from plan_eligibility import filter_plans

SOURCE = 'a' * 64


def plan():
    return {'id': 'plan1', 'passId': 'geometry', 'components': ['body'],
            'operations': ['lathe'], 'fieldPaths': ['geometry.profile'],
            'materialSources': [SOURCE], 'dependencies': ['mesh-engine'],
            'rigHandoff': True}


def scope():
    return {'pass_id': 'geometry', 'pass_unlocked': True, 'components': {'body'},
            'operations': {'lathe'}, 'field_paths': {'geometry.profile'},
            'authorized_sources': {SOURCE}, 'source_operations': {'lathe'},
            'available_dependencies': {'mesh-engine'}, 'rig_required': True, 'rig_available': True}


class PlanPreflightTests(unittest.TestCase):
    def test_admissible_plan_is_detached(self):
        item = plan()
        result = filter_plans([item], **scope())
        self.assertEqual(result['rejected'], [])
        item['components'].append('unauthorized')
        self.assertEqual(result['eligible'][0]['components'], ['body'])

    def test_locked_pass_and_unauthorized_material_are_ineligible(self):
        values = scope()
        values['pass_unlocked'] = False
        values['authorized_sources'] = set()
        result = filter_plans([plan()], **values)
        self.assertEqual(result['eligible'], [])
        self.assertIn('locked-or-wrong-pass', result['rejected'][0]['reasons'])
        self.assertIn('unauthorized-material-source', result['rejected'][0]['reasons'])

    def test_source_required_operation_needs_bound_authorized_asset(self):
        item = plan()
        item['materialSources'] = []
        result = filter_plans([item], **scope())
        self.assertEqual(result['eligible'], [])
        self.assertIn('material-source-required', result['rejected'][0]['reasons'])

    def test_gate_bypass_and_unavailable_rig_are_rejected(self):
        item = plan()
        item['fieldPaths'] = ['quality.threshold']
        item['operations'] = ['disable-gate']
        values = scope()
        values['rig_available'] = False
        reasons = filter_plans([item], **values)['rejected'][0]['reasons']
        self.assertIn('unsupported-fieldPaths', reasons)
        self.assertIn('unsupported-operations', reasons)
        self.assertIn('missing-rig-handoff', reasons)

    def test_unknown_fields_and_duplicate_ids_cannot_enter_consultation(self):
        item = plan()
        item['command'] = 'run something'
        self.assertIn('invalid-plan-shape', filter_plans([item], **scope())['rejected'][0]['reasons'])
        with self.assertRaisesRegex(ValueError, 'invalid-or-duplicate-candidate-id'):
            filter_plans([plan(), copy.deepcopy(plan())], **scope())


if __name__ == '__main__':
    unittest.main()
