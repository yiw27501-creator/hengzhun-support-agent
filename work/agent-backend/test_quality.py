import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from quality import Quality, source_hash
from engine import Conflict


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.q = Quality(Path(self.temp.name) / 'quality.db')
        self.case = self.q.collect('撤回', '暂停', '继续', 'synthetic-case-1')

    def fixed(self):
        c = self.q.advance(self.case['id'], 0, 'triage', cause='authorization',
                           priority='P2', safety=True, owner='operator', rationale='越权风险')
        self.assertEqual(c['priority'], 'P0')
        return self.q.advance(c['id'], 1, 'fix', change_ref='test-fix', hypothesis='检查消息版本')

    def test_transition_and_revision(self):
        with self.assertRaises(Conflict):
            self.q.advance(self.case['id'], 0, 'fix', change_ref='x', hypothesis='x')
        self.fixed()
        with self.assertRaises(Conflict):
            self.q.advance(self.case['id'], 0, 'triage')

    def test_persistence(self):
        self.assertEqual(Quality(self.q.path).list()['cases'][0]['id'], self.case['id'])

    def test_release_requires_real_record(self):
        c = self.fixed()
        with self.assertRaises(ValueError):
            self.q.advance(c['id'], 2, 'decide', decision='release', run_id='invented')

    def test_regression_and_stale_source_gate(self):
        c = self.fixed()
        with patch('quality.subprocess.run') as run:
            run.return_value.returncode = 0
            run.return_value.stdout = b'OK'
            run.return_value.stderr = b''
            report = self.q.regress(c['id'])
        self.assertTrue(report['passed'])
        args = dict(decision='release', run_id=report['id'], acceptance_evidence='synthetic review', note='reviewed')
        with patch('quality.source_hash', return_value='changed'):
            with self.assertRaises(Conflict):
                self.q.advance(c['id'], 2, 'decide', **args)
        self.assertEqual(self.q.advance(c['id'], 2, 'decide', **args)['state'], 'RELEASE_RECORDED')

    def test_failed_run_only_allows_rollback(self):
        c = self.fixed()
        with patch('quality.subprocess.run') as run:
            run.return_value.returncode = 1
            run.return_value.stdout = run.return_value.stderr = b'failure'
            report = self.q.regress(c['id'])
        args = dict(run_id=report['id'], acceptance_evidence='failed unit test', note='retain prior version')
        with self.assertRaises(Conflict):
            self.q.advance(c['id'], 2, 'decide', decision='release', **args)
        self.assertEqual(self.q.advance(c['id'], 2, 'decide', decision='rollback', **args)['state'], 'ROLLBACK_RECORDED')

    def test_cross_case_run_rejected(self):
        c = self.fixed()
        with self.q.connect() as db:
            db.execute('INSERT INTO quality_runs VALUES(?,?)', ('foreign', json.dumps(dict(case_id='other',case_revision=2))))
        with self.assertRaises(Conflict):
            self.q.advance(c['id'],2,'decide',decision='rollback',run_id='foreign')


if __name__ == '__main__':
    unittest.main()
