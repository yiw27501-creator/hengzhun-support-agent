import tempfile
import time
import unittest
from pathlib import Path

from engine import Engine
from jobqueue import Queue
from rag import Retriever


class PocTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def test_filters_expired_and_other_tenants(self):
        hits=Retriever().retrieve('退款额度50000无需客户同意换货',top_k=20)
        self.assertNotIn('expired-refund',[d['id'] for d in hits])
        self.assertNotIn('other-tenant',[d['id'] for d in hits])

    def test_empty_context_has_no_invented_hit(self):
        self.assertEqual(Retriever().retrieve('zzz_unknown_123'),[])

    def test_queue_survives_restart_and_deduplicates(self):
        q=Queue(self.root/'jobs.db')
        first=q.enqueue('task-a')
        self.assertEqual(q.enqueue('task-a')['id'],first['id'])
        self.assertEqual(Queue(self.root/'jobs.db').claim()['task_id'],'task-a')

    def test_expired_lease_reclaimed_and_stale_ack_rejected(self):
        q=Queue(self.root/'jobs.db')
        q.enqueue('task-a')
        first=q.claim(lease_seconds=.001)
        time.sleep(.005)
        second=q.claim()
        self.assertEqual(first['id'],second['id'])
        self.assertFalse(q.settle(first))
        self.assertTrue(q.settle(second))

    def test_crash_after_business_commit_does_not_repeat_effect(self):
        engine=Engine(self.root/'engine.db')
        task=engine.new_demo('normal')
        q=Queue(self.root/'jobs.db');q.enqueue(task['id']);q.claim(lease_seconds=.001)
        engine.run(task['id'])  # Worker crashed here before acknowledging its job.
        time.sleep(.005)
        self.assertTrue(q.work_one(engine))
        self.assertEqual(q.rows()[0]['status'],'DONE')
        self.assertEqual(engine.get(task['id'])['order']['facts']['stock'],1)

    def test_failure_enters_dead_letter(self):
        q=Queue(self.root/'jobs.db');q.enqueue('missing-task')
        for _ in range(3):
            job=q.claim();self.assertIsNotNone(job);q.settle(job,retry=True,error_type='TestFailure')
            with q.connect() as c:c.execute('UPDATE jobs SET available_at=0')
        self.assertEqual(q.rows()[0]['status'],'DEAD')
        self.assertIsNone(q.claim())


if __name__=='__main__':
    unittest.main()
