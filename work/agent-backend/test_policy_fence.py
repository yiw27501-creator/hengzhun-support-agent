import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from engine import Engine,RulePlanner,Conflict
from knowledge import Knowledge


class PolicyFenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.k=Knowledge(self.root/'knowledge.db')
        self.p=self.k.create('refund','policy','退款','合成政策','test')
        for a in ['submit','approve','publish']:
            self.p=self.k.transition('refund',1,self.p['revision'],a,'test')
        self.digest=hashlib.sha256(json.dumps(self.k.documents(),sort_keys=True,ensure_ascii=False).encode()).hexdigest()

    def test_revoke_before_execute_blocks_effect(self):
        k=self.k;p=self.p;digest=self.digest
        class Planner(RulePlanner):
            def plan(self,facts):
                k.transition('refund',1,p['revision'],'retire','revoke during planning')
                return super().plan(facts)
            def execution_guard(self):return k.execution_guard(digest)
        e=Engine(self.root/'business.db',Planner());task=e.new_demo('normal')['id']
        with self.assertRaises(Conflict):e.run(task)
        self.assertIsNone(e.get(task)['receipt'])

    def test_publish_write_waits_for_short_execution_guard(self):
        entered=threading.Event();done=threading.Event();errors=[]
        def retire():
            entered.set()
            try:self.k.transition('refund',1,self.p['revision'],'retire','concurrent revoke')
            except Exception as exc:errors.append(type(exc).__name__)
            finally:done.set()
        with self.k.execution_guard(self.digest):
            t=threading.Thread(target=retire);t.start();self.assertTrue(entered.wait(1))
            self.assertFalse(done.wait(.1))
        t.join(3);self.assertTrue(done.is_set());self.assertEqual(errors,[])


if __name__=='__main__':unittest.main()
