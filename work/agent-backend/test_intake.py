import json
import tempfile
import unittest
from pathlib import Path
from intake import Intake
from engine import Conflict
from import_support_data import build
from faq import catalog

class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=Intake(Path(self.tmp.name)/'intake.db')
        self.messages=[dict(id='m1',role='customer',text='先别退款')]
    def tearDown(self):self.tmp.cleanup()

    def test_trigger_and_dedup(self):
        self.store.submit('event1',self.messages)
        self.store.submit('event1',self.messages)
        with self.store.queue.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM intake_audit').fetchone()[0],1)
        self.assertEqual(self.store.get('event1')['job']['status'],'QUEUED')

    def test_changed_payload_conflicts(self):
        self.store.submit('event1',self.messages)
        with self.assertRaises(Conflict):self.store.submit('event1',[dict(id='m1',role='customer',text='退款')])

    def test_model_disabled_is_explicit(self):
        self.store.submit('event1',self.messages)
        self.store.work_one()
        result=self.store.get('event1')
        self.assertFalse(result['execution_authorized'])
        self.assertEqual(result['result']['next_step'],'human_review_model_disabled')
        self.assertEqual(result['job']['status'],'DONE')

    def test_model_and_budget_hook(self):
        class Stub:
            def extract(_,messages):return dict(valid=True,extraction=dict(needs_clarification=True),execution_authorized=False)
        reserved=[]
        self.store.submit('event1',self.messages)
        self.store.work_one(Stub(),lambda:reserved.append(1))
        self.assertEqual(reserved,[1])
        self.assertEqual(self.store.get('event1')['status'],'NEEDS_CLARIFICATION')

    def test_import_no_external_answers(self):
        raw=b'instruction,intent,category,response\nCan I get a refund?,get_refund,REFUND,Guaranteed instant money\ncan i get a refund!,get_refund,REFUND,bad\nemail a@example.com,get_refund,REFUND,bad\n'
        folder=Path(self.tmp.name)/'dataset'
        result=build(raw,'fixture',folder)
        self.assertEqual(result['kept'],1)
        self.assertEqual(result['duplicates'],1)
        self.assertEqual(result['quarantined'],1)
        faq=catalog(folder/'support.db')
        self.assertEqual(len(faq['items']),1)
        self.assertNotIn('Guaranteed',faq['items'][0]['answer'])
        self.assertFalse(faq['items'][0]['execution_enabled'])

    def test_missing_dataset(self):
        self.assertFalse(catalog(Path(self.tmp.name)/'missing.db')['available'])

    def test_failure_is_bounded_and_sanitized(self):
        class Bad:
            def extract(_,messages):raise RuntimeError('private diagnostic must not be stored')
        self.store.submit('event1',self.messages)
        for _ in range(3):
            with self.store.queue.connect() as c:c.execute('UPDATE jobs SET available_at=0')
            self.store.work_one(Bad())
        result=self.store.get('event1')
        self.assertEqual(result['status'],'DEAD')
        self.assertNotIn('private diagnostic',json.dumps(result))
        self.assertFalse(self.store.work_one(Bad()))

    def test_stale_worker_cannot_save_result(self):
        store=self.store
        class Stale:
            def extract(_,messages):
                with store.queue.connect() as c:c.execute("UPDATE jobs SET lease_token='new-worker'")
                return dict(valid=False,execution_authorized=False)
        store.submit('event1',self.messages)
        store.work_one(Stale())
        self.assertIsNone(store.get('event1')['result'])

if __name__=='__main__':unittest.main()
