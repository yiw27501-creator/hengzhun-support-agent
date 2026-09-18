import unittest
import test_business
from test_business_chat import FakeRouter
from business_chat import BusinessChat
from tool_gateway import ToolGateway, UnconfiguredAdapter, tool_definitions


class ToolGatewayTests(unittest.TestCase):
    setUp = test_business.BusinessTests.setUp

    def setup_task(self):
        self.b.batch('tools', 6)
        return next(x['id'] for x in self.b.list()['items'] if x['kind']=='stock')

    def test_read_scope_and_receipt(self):
        task = self.setup_task(); gateway = ToolGateway(self.e)
        with self.e.connect() as c:
            order = gateway.call(c, task, 'get_order', {})
            self.assertEqual(order['data']['order_id'], self.e.get(task)['order_id'])
            self.assertTrue(order['sandbox'])
            self.assertEqual(gateway.call(c, task, 'get_execution_receipt', {})['data']['status'], 'not_found')
            self.assertEqual(len(tool_definitions()), 5)
            for name, args in [('execute_refund', {}), ('get_order', {'order_id':'OTHER'}), ('get_order', [])]:
                with self.assertRaises(ValueError): gateway.call(c, task, name, args)

    def test_missing_adapter_never_falls_back(self):
        task = self.setup_task()
        self.r.business_router = FakeRouter()
        chat = BusinessChat(self.e, self.r, ToolGateway(self.e, UnconfiguredAdapter()))
        result = chat.submit(task, 'unconfigured', '查一下库存', 1)
        self.assertEqual(result['status'], 'FAILED')
        self.assertEqual(result['tool_call']['error'], 'adapter_unavailable')
        self.assertIsNone(self.e.get(task)['receipt'])
        self.assertEqual(self.b.detail(task)['meta']['journey']['stage'], 'human')

    def test_timeout_and_invalid_response(self):
        task = self.setup_task()
        class TimeoutAdapter:
            def read(self, *args): raise TimeoutError('secret must not escape')
        class InvalidAdapter:
            def read(self, *args): return {'stock':-1}
        with self.e.connect() as c:
            for adapter, code in [(TimeoutAdapter(), 'timeout'), (InvalidAdapter(), 'adapter_unavailable')]:
                result = ToolGateway(self.e, adapter).call(c, task, 'check_inventory', {})
                self.assertFalse(result['ok']);self.assertEqual(result['error'], code)
                self.assertNotIn('secret', str(result))

    def test_model_path_uses_adapter_values(self):
        task = self.setup_task()
        class InventoryAdapter:
            def read(self, *args):
                return dict(sku='ALT-B', stock=0, price_minor=12300, checked_at='2026-09-17')
        self.r.business_router = FakeRouter()
        result = BusinessChat(self.e, self.r, ToolGateway(self.e, InventoryAdapter())).submit(task, 'zero', '查库存', 1)
        self.assertIn('ALT-B 有 0 件', result['reply'])
        self.assertFalse(result['tool_call']['sandbox'])
        self.assertEqual(self.e.get(task)['order']['facts']['stock'], 0)
        self.assertIsNone(self.e.get(task)['receipt'])


if __name__ == '__main__': unittest.main()
