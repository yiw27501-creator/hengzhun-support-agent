import tempfile
import unittest
from pathlib import Path
from engine import Engine
from intake import Intake
from intent_router import validate

class SplitRouterTests(unittest.TestCase):
    def test_intent_and_pause_coexist(self):
        messages=[dict(id='m',role='customer',text='先别退款')]
        result=dict(intent='get_refund',dialogue_state='paused',question='',reason='客户暂停',evidence=[dict(message_id='m',quote='先别退款')])
        self.assertTrue(validate(result,messages))
        result['question']='现在确认吗'
        self.assertFalse(validate(result,messages))

    def test_fabricated_quote_blocked(self):
        result=dict(intent='get_refund',dialogue_state='ready',question='',reason='x',evidence=[dict(message_id='m',quote='同意退款')])
        self.assertFalse(validate(result,[dict(id='m',role='customer',text='暂停')]))

    def test_outbox_restart_dedup_and_stale_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine=Engine(Path(tmp)/'engine.db');intake=Intake(Path(tmp)/'intake.db')
            task=engine.new_demo('normal')['id']
            engine.append_message(task,'m1','退款',0)
            engine=Engine(Path(tmp)/'engine.db')
            engine.dispatch_understanding(intake)
            # Simulate crash between external delivery and outbox acknowledgement.
            with engine.connect() as c:c.execute("UPDATE understanding_outbox SET state='PENDING'")
            engine.dispatch_understanding(intake)
            with intake.queue.connect() as c:self.assertEqual(c.execute('SELECT count(*) FROM jobs').fetchone()[0],1)
            engine.append_message(task,'m2','先别操作',1)
            intake.work_one();engine.collect_understanding(intake)
            with engine.connect() as c:self.assertEqual(c.execute('SELECT state FROM understanding_outbox WHERE version=1').fetchone()[0],'SUPERSEDED')
            engine.dispatch_understanding(intake);intake.work_one();engine.collect_understanding(intake)
            current=engine.get(task)
            self.assertEqual(current['understanding']['version'],2)
            self.assertIsNone(current['order']['facts']['authorized_version'])
            self.assertIsNone(current['receipt'])

    def test_paused_intake_state(self):
        class Stub:
            def extract(self,m):return dict(valid=True,extraction=dict(dialogue_state='paused',needs_clarification=False),execution_authorized=False)
        with tempfile.TemporaryDirectory() as tmp:
            intake=Intake(Path(tmp)/'intake.db')
            intake.submit('e',[dict(id='m',role='customer',text='暂停')])
            intake.work_one(Stub())
            self.assertEqual(intake.get('e')['status'],'PAUSED')

if __name__=='__main__':unittest.main()
