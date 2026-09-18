import json
import tempfile
import unittest
from pathlib import Path
from quality import Quality


class ProductTests(unittest.TestCase):
    def test_empty_metrics_not_fake_percent(self):
        with tempfile.TemporaryDirectory() as folder:
            q=Quality(Path(folder)/'q.db');m=q.metrics()
            self.assertIsNone(m['regression_pass_rate']);self.assertIsNone(m['p0_change_rate'])
            self.assertEqual(m['total_cases'],0)

    def test_metrics_reflect_triage_and_fix(self):
        with tempfile.TemporaryDirectory() as folder:
            q=Quality(Path(folder)/'q.db');c=q.collect('x','y','z','test')
            q.advance(c['id'],0,'triage',cause='authorization',priority='P2',safety=True,owner='test',rationale='test')
            q.advance(c['id'],1,'fix',change_ref='test',hypothesis='test')
            m=q.metrics();self.assertEqual(m['open_p0'],1);self.assertEqual(m['p0_triaged_last_7d'],1)
            self.assertEqual(m['fix_sample_count'],1);self.assertEqual(m['causes']['authorization'],1)

    def test_safety_cases_are_drafts_not_results(self):
        data=json.loads(Path(__file__).with_name('safety_cases.json').read_text(encoding='utf-8'))
        self.assertEqual(len(data['cases']),25)
        self.assertEqual(len({c['id'] for c in data['cases']}),25)
        for c in data['cases']:
            self.assertFalse(c['execution_authorized'])
            self.assertEqual(c['review_status'],'needs_independent_review')
            self.assertNotIn('passed',c)


if __name__=='__main__':unittest.main()
