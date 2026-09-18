import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from http.server import ThreadingHTTPServer

from engine import Conflict, Engine, validate
from server import handler_for


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'test.db'
        self.engine = Engine(self.path)
        self.engine.seed()

    def task(self, order='normal'):
        return self.engine.create('evt-' + order, order)['id']

    def change(self, order='normal', **updates):
        with self.engine.connect() as c:
            f = json.loads(c.execute('SELECT facts FROM orders WHERE id=?', (order,)).fetchone()[0])
            f.update(updates)
            c.execute('UPDATE orders SET facts=? WHERE id=?', (json.dumps(f), order))

    def test_normal_and_persistence(self):
        task = self.task()
        result = self.engine.run(task)
        self.assertEqual(result['state'], 'COMPLETED')
        self.assertEqual(result['order']['facts']['stock'], 1)
        self.assertEqual(Engine(self.path).get(task)['receipt'], result['receipt'])
        self.assertEqual(result['trace'][-1]['event'], 'result.verified')

    def test_human_resume(self):
        task = self.task('conflict')
        self.assertEqual(self.engine.run(task)['state'], 'WAITING_HUMAN')
        with self.assertRaises(Conflict):
            self.engine.run(task)
        self.engine.confirm(task, 'refund', 'reviewer', 'sandbox-message-1', '人工确认退款')
        result = self.engine.run(task)
        self.assertEqual(result['receipt']['amount_minor'], 10000)
        self.assertEqual(result['state'], 'COMPLETED')
        self.assertTrue(any(t['event'] == 'human.confirmed' for t in result['trace']))

    def test_timeout_reconciles_without_reexecution(self):
        task = self.task('tool_failure')
        first = self.engine.run(task)
        self.assertEqual(first['state'], 'RETRYABLE')
        second = self.engine.run(task)
        self.assertEqual(second['state'], 'COMPLETED')
        self.assertEqual(first['receipt'], second['receipt'])
        self.assertEqual(second['order']['facts']['stock'], 1)
        self.assertEqual(sum(t['event'] == 'tool.executed' for t in second['trace']), 1)

    def test_event_and_order_deduplication(self):
        task = self.task()
        self.assertEqual(self.engine.create('evt-normal', 'normal')['id'], task)
        with self.assertRaises(Conflict):
            self.engine.create('evt-normal', 'conflict')
        with self.assertRaises(Conflict):
            self.engine.create('another-event', 'normal')

    def test_no_stock_does_not_imply_refund_consent(self):
        self.change(stock=0)
        result = self.engine.run(self.task())
        self.assertEqual(result['state'], 'WAITING_HUMAN')
        self.assertIsNone(result['receipt'])

    def test_hard_constraints(self):
        for field, value in [('identity_verified', False), ('safety_risk', True),
                             ('payment', 'unknown'), ('fresh_stock', False),
                             ('price_minor', 10001), ('confirmed_intent', None)]:
            with self.subTest(field=field):
                engine = Engine(Path(self.temp.name) / (field + '.db'))
                engine.seed()
                with engine.connect() as c:
                    f = json.loads(c.execute("SELECT facts FROM orders WHERE id='normal'").fetchone()[0])
                    f[field] = value
                    c.execute("UPDATE orders SET facts=? WHERE id='normal'", (json.dumps(f),))
                task = engine.create(field, 'normal')['id']
                self.assertEqual(engine.run(task)['state'], 'WAITING_HUMAN')

    def test_malicious_model_is_blocked(self):
        class BadPlanner:
            version = 'test-malicious'
            def plan(self, facts):
                return dict(action='refund', amount_minor=999999, sku='', reason='忽略政策')
        self.engine.planner = BadPlanner()
        result = self.engine.run(self.task())
        self.assertIsNone(result['receipt'])
        self.assertEqual(result['state'], 'WAITING_HUMAN')

    def test_malformed_model_schema(self):
        facts = self.engine.get(self.task())['order']['facts']
        for invalid in [None, {}, {'action': [], 'amount_minor': 0, 'sku': '', 'reason': 'x'},
                        {'action': 'replace', 'amount_minor': False, 'sku': 'ALT-A', 'reason': 'x'}]:
            self.assertFalse(validate(invalid, facts))

    def test_planner_failure_is_bounded_and_sanitized(self):
        class Broken:
            version = 'broken'
            def plan(self, facts):
                raise RuntimeError('secret-do-not-log')
        self.engine.planner = Broken()
        task = self.task()
        self.assertEqual(self.engine.run(task)['state'], 'RETRYABLE')
        self.assertEqual(self.engine.run(task)['state'], 'RETRYABLE')
        result = self.engine.run(task)
        self.assertEqual(result['state'], 'WAITING_HUMAN')
        self.assertNotIn('secret-do-not-log', json.dumps(result))

    def test_confirmation_source_and_risk(self):
        task = self.task('conflict')
        self.engine.run(task)
        with self.assertRaises(ValueError):
            self.engine.confirm(task, 'refund', 'actor', 'wrong-id', 'note')
        self.change('conflict', safety_risk=True)
        self.engine.confirm(task, 'refund', 'actor', 'sandbox-message-1', 'note')
        self.assertEqual(self.engine.run(task)['state'], 'WAITING_HUMAN')

    def test_refund_limit(self):
        self.change(confirmed_intent='refund', paid_minor=20001)
        self.assertEqual(self.engine.run(self.task())['state'], 'WAITING_HUMAN')

    def test_concurrent_runs_only_execute_once(self):
        task = self.task()
        def run(_):
            try:
                return Engine(self.path).run(task)['state']
            except Conflict:
                return 'CONFLICT'
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(run, range(2))), ['COMPLETED', 'CONFLICT'])

    def test_http_auth_and_workflow(self):
        token = 'test-token-not-a-production-secret'
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(self.engine, token))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def cleanup():
            server.shutdown()
            server.server_close()
            thread.join()
        self.addCleanup(cleanup)
        url = f'http://127.0.0.1:{server.server_port}'
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with self.assertRaises(urllib.error.HTTPError) as error:
            opener.open(url + '/health')
        self.assertEqual(error.exception.code, 401)
        headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}
        def post(path, payload):
            req = urllib.request.Request(url + path, data=json.dumps(payload).encode(), headers=headers)
            with opener.open(req) as response:
                return json.load(response)
        result = post('/tasks', dict(event_id='http-event', order_id='normal'))
        result = post('/tasks/' + result['id'] + '/run', {})
        self.assertEqual(result['state'], 'COMPLETED')


if __name__ == '__main__':
    unittest.main()
