import json
import tempfile
import unittest
from pathlib import Path
from engine import Engine,Conflict
from intake import Intake

class ClarificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.engine=Engine(Path(self.tmp.name)/'engine.db');self.intake=Intake(Path(self.tmp.name)/'intake.db')
        self.task=self.engine.new_demo('normal')['id']
    def ask(self):
        self.engine.append_message(self.task,'m1','帮我处理一下',0)
        self.engine.dispatch_understanding(self.intake)
        class Stub:
            def extract(self,m):return dict(valid=True,extraction=dict(needs_clarification=True,question='您希望退款还是换货？'))
        self.intake.work_one(Stub());self.engine.collect_understanding(self.intake)
        return self.engine.get(self.task)['clarification']

    def test_reply_bound_and_agent_message_in_context(self):
        q=self.ask()
        self.engine.append_message(self.task,'m2','退款',1,q['id'])
        with self.engine.connect() as c:
            row=c.execute('SELECT payload FROM understanding_outbox WHERE version=2').fetchone()
            self.assertEqual([m['role'] for m in json.loads(row['payload'])],['customer','agent','customer'])
            self.assertEqual(c.execute('SELECT state FROM clarifications').fetchone()[0],'ANSWERED')
        self.engine.append_message(self.task,'m2','退款',1,q['id'])
        with self.assertRaises(Conflict):self.engine.append_message(self.task,'m2','退款',1)
        self.assertIsNone(self.engine.get(self.task)['receipt'])

    def test_wrong_task_reply_rejected(self):
        q=self.ask();other=self.engine.new_demo('normal')['id']
        with self.assertRaises(Conflict):self.engine.append_message(other,'m2','退款',0,q['id'])

    def test_old_question_superseded_by_new_message(self):
        q=self.ask();self.engine.append_message(self.task,'m2','先等等',1)
        with self.assertRaises(Conflict):self.engine.append_message(self.task,'m3','退款',2,q['id'])

    def test_recollect_does_not_duplicate_question(self):
        self.ask();self.engine.collect_understanding(self.intake)
        with self.engine.connect() as c:self.assertEqual(c.execute('SELECT count(*) FROM clarifications').fetchone()[0],1)
