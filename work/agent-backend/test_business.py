import tempfile
import unittest
from pathlib import Path
from engine import Engine,Conflict
from runtime import Runtime
from business import Business


class BusinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.e=Engine(self.root/'business.db')
        self.r=Runtime(self.root/'business.db',self.root/'queue.db')
        self.b=Business(self.e,self.r)

    def test_batch_idempotence_and_metrics(self):
        self.b.batch('test',6);self.b.batch('test',6)
        self.assertEqual(len(self.b.list()['items']),6)
        for _ in range(12):self.r.queue.work_one(self.e)
        # Timeout reattempt has a short backoff; reconcile directly for a deterministic test.
        for x in self.b.list()['items']:
            if x['state']=='RETRYABLE':self.e.run(x['id'])
        m=self.b.list()['metrics'];self.assertEqual(m['automatic'],3)
        self.assertEqual(m['human_waiting'],0);self.assertEqual(m['automatic_rate'],.5)
        self.assertEqual(sum(x['status']=='waiting' for x in self.b.list()['items']),3)

    def test_verification_requires_two_steps_and_explicit_choice(self):
        self.b.batch('journey',6)
        task=next(x['id'] for x in self.b.list()['items'] if x['kind']=='identity')
        with self.assertRaises(Conflict):self.b.advance(task,'refund',1)
        self.b.advance(task,'verify_recipient',1)
        self.assertFalse(self.e.get(task)['order']['facts']['identity_verified'])
        with self.assertRaises(Conflict):self.b.advance(task,'buyer_authorize',1)
        self.b.advance(task,'buyer_authorize',2)
        self.assertIsNone(self.e.get(task)['receipt'])
        self.b.advance(task,'refund',3)
        result=self.e.run(task)
        self.assertIsNotNone(result['receipt'])
        self.assertFalse(result['order']['facts']['human_confirmed'])

    def test_inventory_refresh_and_pause_information_do_not_execute(self):
        self.b.batch('journey',6)
        stock=next(x['id'] for x in self.b.list()['items'] if x['kind']=='stock')
        self.assertTrue(self.e.get(stock)['order']['facts']['fresh_stock'])
        self.assertIsNone(self.e.get(stock)['receipt'])
        self.b.advance(stock,'replace',1)
        self.assertIsNotNone(self.e.run(stock)['receipt'])
        paused=next(x['id'] for x in self.b.list()['items'] if x['kind']=='conflict')
        self.b.advance(paused,'product_info',1)
        self.assertIsNone(self.e.run(paused)['receipt'])
        self.assertEqual(self.b.detail(paused)['meta']['journey']['stage'],'paused')

    def test_live_batch_rejected(self):
        self.r.key='fixture-not-real'
        with self.assertRaises(Conflict):self.b.batch('test',6)
        self.assertEqual(self.b.list()['metrics']['total'],0)

    def test_unsafe_handoff_cannot_bypass_identity(self):
        self.b.batch('test',6)
        x=next(x for x in self.b.list()['items'] if x['kind']=='identity')
        t=self.e.run(x['id']);self.assertIsNone(t['receipt'])
        self.e.confirm(x['id'],'refund','test',t['order']['facts']['message_id'],'synthetic confirmation',expected_version=1)
        t=self.e.run(x['id']);self.assertIsNone(t['receipt']);self.assertEqual(t['state'],'WAITING_HUMAN')

    def test_conflict_and_empty_metrics(self):
        self.assertIsNone(self.b.list()['metrics']['automatic_rate'])
        self.b.batch('test',6)
        with self.assertRaises(Conflict):self.b.batch('test',30)
        with self.assertRaises(ValueError):self.b.batch('bad',10000)


if __name__=='__main__':unittest.main()
