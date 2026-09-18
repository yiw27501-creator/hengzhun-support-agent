import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from engine import Engine,Conflict
from knowledge import Knowledge
from quality import Quality
from jobqueue import Queue
from rag import Retriever


class GovernanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.k=Knowledge(self.root/'knowledge.db');self.q=Quality(self.root/'quality.db')

    def policy(self,**extra):
        return self.k.create(id='refund',kind='policy',title='退款政策',text='退款金额需核对',source='合成',**extra)

    def publish(self,d):
        for action in ['submit','approve','publish']:
            d=self.k.transition(d['id'],d['version'],d['revision'],action,'测试审核')
        return d

    def test_draft_and_tenant_filtered(self):
        d=self.policy();r=Retriever(knowledge=self.k)
        self.assertEqual(r.retrieve('退款'),[])
        self.publish(d);self.assertTrue(r.retrieve('退款'));self.assertEqual(r.retrieve('退款',tenant='other'),[])

    def test_faq_revocation_and_versions(self):
        d=self.publish(self.policy())
        f=self.k.create(id='faq',kind='faq',title='退款如何处理',text='核对政策',source='合成',policy_refs=['refund@1'])
        self.publish(f);self.assertEqual(len(self.k.active()),2)
        self.k.transition(d['id'],1,d['revision'],'retire','撤销')
        self.assertEqual(self.k.active(),[])
        v2=self.publish(self.policy());self.assertEqual(v2['version'],2)
        self.assertEqual(len(self.k.active()),1)

    def test_invalid_reference_expiry_and_stale(self):
        d=self.policy(effective_until='2021-01-01T00:00:00Z')
        with self.assertRaises(Conflict):self.publish(d)
        f=self.k.create(id='faq',kind='faq',title='Q',text='A',source='synthetic',policy_refs=['missing@1'])
        with self.assertRaises(Conflict):self.publish(f)
        with self.assertRaises(Conflict):self.k.transition(d['id'],1,0,'submit','stale')

    def test_automatic_discovery_deduplicates(self):
        e=Engine(self.root/'business.db');task=e.new_demo('normal')['id'];queue=Queue(self.root/'queue.db')
        with e.connect() as c:e.trace(c,task,'guardrail.blocked',reason='synthetic')
        queue.enqueue(task)
        with queue.connect() as c:c.execute("UPDATE jobs SET status='DEAD'")
        self.assertEqual(self.q.scan(e,[queue]),2);self.assertEqual(self.q.scan(e,[queue]),0)
        self.assertTrue(all(c['state']=='NEW' for c in self.q.list()['cases']))

    def fixed(self):
        c=self.q.collect('model','paused','ready','synthetic')
        self.q.advance(c['id'],0,'triage',cause='prompt',priority='P0',safety=True,owner='test',rationale='safety')
        self.q.protocol(c['id'],[dict(id='a',expected='paused',safety=True)])
        return self.q.advance(c['id'],1,'fix',change_ref='candidate',hypothesis='handle withdrawal')

    def test_model_gate_recomputes_and_requires_evidence(self):
        case=self.fixed()
        records=[dict(id='a',prediction='paused',response_id='fixture-not-live',model='test',prompt_hash='fixture')]
        failed=self.q.evaluate(case['id'],records,[dict(records[0],prediction='ready')])
        self.assertFalse(failed['passed'])
        with patch('quality.subprocess.run') as run:
            run.return_value.returncode=0;run.return_value.stdout=run.return_value.stderr=b''
            regression=self.q.regress(case['id'])
        args=dict(decision='release',run_id=regression['id'],acceptance_evidence='fixture',note='test')
        with self.assertRaises(Conflict):self.q.advance(case['id'],2,'decide',**args)
        with self.assertRaises(Conflict):self.q.advance(case['id'],2,'decide',evaluation_id=failed['id'],**args)
        passed=self.q.evaluate(case['id'],records,records)
        self.assertEqual(self.q.advance(case['id'],2,'decide',evaluation_id=passed['id'],**args)['state'],'RELEASE_RECORDED')

    def test_frozen_set_cannot_be_changed_or_partially_evaluated(self):
        case=self.fixed()
        with self.assertRaises(Conflict):self.q.protocol(case['id'],[dict(id='b',expected='x',safety=True)])
        with self.assertRaises(ValueError):self.q.evaluate(case['id'],[],[])


if __name__=='__main__':unittest.main()
