import unittest
from iterate_intent_routing import choose_version, summarize

class SelectionTests(unittest.TestCase):
    def cases(self):
        dev=[dict(question=str(i),correct=True) for i in range(30)]
        challenge=[dict(expected='unknown' if i<3 else 'get_refund',correct=True) for i in range(8)]
        return dev,challenge

    def test_pass(self):
        dev,challenge=self.cases()
        self.assertEqual(choose_version(dev,dev,challenge,set())['selected'],'v2')

    def test_safety_failure_blocks_even_at_seven_of_eight(self):
        dev,challenge=self.cases();challenge[0]['correct']=False
        self.assertEqual(choose_version(dev,dev,challenge,set())['selected'],'v1')

    def test_regression_blocks(self):
        dev,challenge=self.cases();candidate=[dict(r) for r in dev];candidate[0]['correct']=False
        self.assertEqual(choose_version(dev,candidate,challenge,set())['selected'],'v1')

    def test_disputed_exclusion_does_not_change_report_accuracy(self):
        dev,challenge=self.cases();candidate=[dict(r) for r in dev];candidate[0]['correct']=False
        self.assertEqual(choose_version(dev,candidate,challenge,{'0'})['selected'],'v2')
        self.assertEqual(summarize(candidate)['correct'],29)

    def test_incomplete_run_cannot_pass(self):
        dev,challenge=self.cases()
        self.assertEqual(choose_version(dev,dev[:29],challenge,set())['selected'],'v1')
