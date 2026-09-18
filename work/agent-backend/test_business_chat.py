import unittest
import test_business
from business_chat import BusinessChat
from engine import Conflict

class FakeRouter:
    def __init__(self,tool='inventory'):self.tool=tool;self.calls=0
    def route(self,context):
        self.calls+=1
        return dict(decision=dict(tool=self.tool,quote=context['latest']),model='TEST-DOUBLE',response_id='test',usage={'total_tokens':0},latency_ms=0)

class BusinessChatTests(unittest.TestCase):
    setUp=test_business.BusinessTests.setUp

    def setup_chat(self,tool='inventory'):
        self.b.batch('chat',6)
        self.task=next(x['id'] for x in self.b.list()['items'] if x['kind']=='stock')
        self.r.business_router=FakeRouter(tool)
        self.chat=BusinessChat(self.e,self.r)

    def test_model_tool_confirmation_queue_receipt(self):
        self.setup_chat()
        for _ in range(6):self.r.queue.work_one(self.e)
        response=self.chat.submit(self.task,'new-message','页面没货，先查库存，不要退款',1)
        self.assertEqual(response['status'],'DONE')
        self.assertEqual(response['tool_result']['provider'],'simulated-inventory')
        self.assertIsNone(self.e.get(self.task)['receipt'])
        self.assertIsNone(self.e.get(self.task)['order']['facts']['authorized_version'])
        self.chat.submit(self.task,'new-message','页面没货，先查库存，不要退款',1)
        self.assertEqual(self.r.business_router.calls,1)
        j=self.b.detail(self.task)['meta']['journey']
        self.b.advance(self.task,'replace',j['version'])
        for _ in range(12):self.r.queue.work_one(self.e)
        self.assertEqual(self.e.get(self.task)['receipt']['action'],'replace')

    def test_invalid_tool_never_executes(self):
        self.setup_chat('execute_refund')
        r=self.chat.submit(self.task,'bad','立即退款',1)
        self.assertEqual(r['status'],'FAILED')
        self.assertIsNone(self.e.get(self.task)['receipt'])
        self.assertIsNone(self.e.get(self.task)['order']['facts']['confirmed_intent'])

    def test_new_message_supersedes_slow_model(self):
        self.setup_chat()
        original=self.r.business_router.route
        def delayed(context):
            self.e.append_message(self.task,'withdraw','先别处理',2)
            return original(context)
        self.r.business_router.route=delayed
        r=self.chat.submit(self.task,'slow','查库存',1)
        self.assertEqual(r['status'],'SUPERSEDED')
        self.assertIsNone(self.e.get(self.task)['receipt'])

    def test_disabled_and_budget_fail_closed(self):
        self.setup_chat();self.r.max_calls=0
        r=self.chat.submit(self.task,'budget','查库存',1)
        self.assertEqual(r['status'],'FAILED')
        self.assertEqual(self.r.business_router.calls,0)
        del self.r.business_router
        with self.assertRaises(Conflict):self.chat.submit(self.task,'disabled','查库存',2)

    def test_pause_and_purchase_do_not_authorize(self):
        self.setup_chat('pause')
        self.chat.submit(self.task,'pause','先不要处理',1)
        self.assertEqual(self.b.detail(self.task)['meta']['journey']['stage'],'paused')
        self.r.business_router.tool='purchase'
        self.chat.submit(self.task,'purchase','朋友买的',2)
        f=self.e.get(self.task)['order']['facts']
        self.assertFalse(f['identity_verified']);self.assertIsNone(f['authorized_version'])
