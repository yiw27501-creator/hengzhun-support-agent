import unittest
import test_business
from engine import Conflict


class ReturnTests(unittest.TestCase):
    setUp = test_business.BusinessTests.setUp
    def create_return(self):
        self.b.batch('return-demo',1,profile='return')
        return self.b.list()['items'][0]['id']

    def step(self,task,action):
        return self.b.advance(task,action,self.b.detail(task)['meta']['journey']['version'])

    def test_return_happy_path_and_no_early_refund(self):
        task=self.create_return()
        self.r.queue.work_one(self.e)
        for action in ['propose_return','accept_return','ship_return','receive_return']:
            self.step(task,action)
            self.assertIsNone(self.e.get(task)['receipt'])
            with self.assertRaises(Conflict):self.e.run(task)
        self.step(task,'pass_inspection')
        self.r.queue.work_one(self.e)
        result=self.e.get(task)
        self.assertEqual(result['receipt']['action'],'refund')
        self.assertTrue(result['order']['facts']['human_confirmed'])
        with self.assertRaises(Conflict):self.step(task,'pass_inspection')

    def test_out_of_order_stale_and_failed_inspection(self):
        task=self.create_return()
        with self.assertRaises(Conflict):self.step(task,'pass_inspection')
        self.step(task,'propose_return')
        with self.assertRaises(Conflict):self.b.advance(task,'accept_return',1)
        for action in ['accept_return','ship_return','logistics_exception','retry_logistics','receive_return','fail_inspection']:
            self.step(task,action)
        self.assertIsNone(self.e.get(task)['receipt'])
        self.assertEqual(self.b.list()['items'][0]['status'],'human')
        self.step(task,'reinspect');self.step(task,'pass_inspection')
        self.assertIsNotNone(self.e.run(task)['receipt'])

    def test_manual_confirmation_cannot_skip_warehouse(self):
        task=self.create_return();self.e.run(task)
        facts=self.e.get(task)['order']['facts']
        self.e.confirm(task,'refund','test',facts['message_id'],'test',expected_version=1)
        self.assertIsNone(self.e.run(task)['receipt'])
