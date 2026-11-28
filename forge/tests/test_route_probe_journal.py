import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '_shared'))
from decision_control import DecisionController, DecisionError


class ProbeJournalTests(unittest.TestCase):
    def test_probe_is_persisted_and_lease_is_enforced(self):
        with tempfile.TemporaryDirectory() as workspace:
            controller = DecisionController(workspace, 'run-1')
            probe = {'argv': ['img2', '--version', '--json'], 'exitCode': 0,
                     'stdoutSha256': hashlib.sha256(b'{}').hexdigest(), 'at': 10}
            result = controller.record_probe(probe)
            entries = [json.loads(line) for line in
                       (Path(workspace) / '.img2threejs/route/journal.jsonl').read_text().splitlines()]
            self.assertEqual(entries[-1]['hash'], result)
            self.assertEqual(entries[-1]['event'], 'probe')
            self.assertEqual(entries[-1]['data']['argv'], probe['argv'])
            self.assertNotIn('fallbackAuthorized', entries[-1]['data'])
            controller.release()
            with self.assertRaises(DecisionError) as failure:
                controller.record_probe(probe)
            self.assertEqual(failure.exception.reason_code, 'lease-lost')

    def test_bad_probe_is_not_written(self):
        with tempfile.TemporaryDirectory() as workspace:
            controller = DecisionController(workspace, 'run-1')
            path = Path(workspace) / '.img2threejs/route/journal.jsonl'
            original = path.read_bytes()
            with self.assertRaises(DecisionError):
                controller.record_probe({'argv': ['img2'], 'exitCode': True,
                                         'stdoutSha256': '0' * 64, 'at': 1})
            self.assertEqual(path.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
