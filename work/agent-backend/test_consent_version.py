import tempfile
import threading
import unittest
from pathlib import Path
from engine import Engine, Conflict, RulePlanner, eligibility

class ConsentVersionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'test.db';self.engine=Engine(self.path)
        self.task=self.engine.new_demo('normal')['id']

    def test_withdraw_and_resume(self):
        self.engine.append_message(self.task,'m1','先别退款',0)
        with self.assertRaises(Conflict):self.engine.run(self.task)
        self.assertIsNone(self.engine.get(self.task)['receipt'])
        self.engine.append_message(self.task,'m2','想好了，确认退款',1)
        with self.assertRaises(Conflict):self.engine.confirm(self.task,'refund','operator','m2','review',1)
        with self.assertRaises(Conflict):self.engine.confirm(self.task,'refund','operator','m2','review')
        self.engine.confirm(self.task,'refund','operator','m2','已核对当前原话',2)
        result=self.engine.run(self.task)
        self.assertEqual(result['state'],'COMPLETED')
        self.assertEqual(result['receipt']['action'],'refund')

    def test_duplicate_and_conflict(self):
        self.engine.append_message(self.task,'m1','暂停',0)
        self.engine.append_message(self.task,'m1','暂停',0)
        self.assertEqual(self.engine.get(self.task)['order']['facts']['dialogue_version'],1)
        with self.assertRaises(Conflict):self.engine.append_message(self.task,'m1','退款',0)
        with self.assertRaises(Conflict):self.engine.append_message(self.task,'m2','退款',0)

    def test_cannot_retroactively_cancel_effect(self):
        receipt=self.engine.run(self.task)['receipt']
        with self.assertRaises(Conflict):self.engine.append_message(self.task,'m1','暂停',0)
        self.assertEqual(self.engine.get(self.task)['receipt'],receipt)

    def test_guard_rejects_stale_authorization(self):
        facts=self.engine.get(self.task)['order']['facts']
        facts.update(dialogue_version=2,authorized_version=1)
        self.assertEqual(eligibility(facts),'human')

    def test_withdraw_during_model_call(self):
        started=threading.Event();release=threading.Event();outcomes=[]
        class Slow(RulePlanner):
            def plan(self,facts):
                started.set()
                if not release.wait(5):raise RuntimeError('test timeout')
                return super().plan(facts)
        def run():
            try:outcomes.append(Engine(self.path,Slow()).run(self.task))
            except Conflict:outcomes.append('stale_blocked')
        thread=threading.Thread(target=run);thread.start()
        try:
            self.assertTrue(started.wait(2))
            self.engine.append_message(self.task,'m1','先不要操作',0)
        finally:
            release.set();thread.join(6)
        self.assertFalse(thread.is_alive())
        self.assertEqual(outcomes,['stale_blocked'])
        self.assertIsNone(self.engine.get(self.task)['receipt'])

if __name__=='__main__':unittest.main()
