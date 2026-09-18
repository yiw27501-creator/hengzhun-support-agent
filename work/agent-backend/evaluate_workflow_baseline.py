"""Frozen synthetic HTTP and queue evaluation. No paid model or real business API."""
import argparse
import hashlib
import json
import secrets
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from engine import Engine
from runtime import Runtime
from server import handler_for

ROOT = Path(__file__).parent
FIXTURE = ROOT / 'workflow_eval_cases.json'


class FixtureRouter:
    def route(self, context):
        text = context['latest']
        tool = 'execute_refund' if '非法' in text else 'pause'
        return dict(decision=dict(tool=tool, quote=text), model='DETERMINISTIC-DOUBLE',
                    response_id='synthetic', usage={'total_tokens': 0}, latency_ms=0)


def evaluate():
    fixture_bytes = FIXTURE.read_bytes()
    fixture = json.loads(fixture_bytes)
    expected = {case['id']: case['expected'] for case in fixture['cases']}
    with tempfile.TemporaryDirectory() as folder:
        db = Path(folder) / 'business.db'
        engine = Engine(db)
        runtime = Runtime(db, Path(folder) / 'queue.db', max_calls=3)
        runtime.business_router = FixtureRouter()
        token = secrets.token_urlsafe(32)
        service = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(engine, token, desktop=True, runtime=runtime))
        thread = threading.Thread(target=service.serve_forever, daemon=True)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def api(path, body=None):
            request = urllib.request.Request(
                f'http://127.0.0.1:{service.server_port}/api/workflow/{path}',
                data=json.dumps(body).encode() if body is not None else None,
                headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
            try:
                with opener.open(request, timeout=10) as response:
                    return response.status, json.load(response)
            except urllib.error.HTTPError as error:
                return error.code, json.load(error)

        def detail(task_id):
            status, result = api('business/' + task_id)
            assert status == 200, result
            return result

        def wait_done(task_id):
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                status, result = api('tasks/' + task_id + '/job')
                if status == 200 and result['job'] and result['job']['status'] in {'DONE', 'DEAD'}:
                    return result['job']
                time.sleep(.03)
            raise TimeoutError('Queue did not settle for ' + task_id)

        def batch(event_id, count=6, profile='shortage'):
            status, result = api('business/batch', dict(event_id=event_id, count=count, profile=profile))
            assert status == 201, result
            with engine.connect() as connection:
                ids = [row[0] for row in connection.execute(
                    'SELECT task_id FROM business_tickets WHERE batch_id=?', (event_id,))]
            return {detail(task_id)['meta']['kind']: task_id for task_id in ids}

        def advance(task_id, action):
            version = detail(task_id)['meta']['journey']['version']
            return api('business/' + task_id + '/advance', dict(action=action, expected_version=version))

        def receipt_count(task_id):
            order_id = detail(task_id)['task']['order_id']
            with engine.connect() as connection:
                return connection.execute('SELECT COUNT(*) FROM executions WHERE order_id=?', (order_id,)).fetchone()[0]

        actual = {}
        thread.start()
        try:
            first = batch('eval-safety-v1')
            paused_id = first['replace']
            status, response = api('business/' + paused_id + '/chat',
                                   dict(request_id='eval-pause', text='先不要处理', expected_version=1))
            assert status == 200 and response['status'] == 'DONE', response
            invalid_id = first['stock']
            status, invalid = api('business/' + invalid_id + '/chat',
                                  dict(request_id='eval-invalid', text='非法动作测试', expected_version=1))
            assert status == 200, invalid
            runtime.start()
            for task_id in first.values():
                wait_done(task_id)
            paused = detail(paused_id)['task']
            actual['withdraw_before_worker'] = dict(
                state=paused['state'], receipt=paused['receipt'] is not None,
                authorization_active=paused['order']['facts']['authorized_version'] is not None)
            invalid_task = detail(invalid_id)['task']
            actual['invalid_model_tool'] = dict(
                route_status=invalid['status'], receipt=invalid_task['receipt'] is not None,
                authorization_active=invalid_task['order']['facts']['authorized_version'] is not None)

            second = batch('eval-workflow-v1')
            for task_id in second.values():
                wait_done(task_id)
            for kind, case_id in [('replace', 'automatic_replace'), ('refund', 'automatic_refund')]:
                task_id = second[kind]
                task = detail(task_id)['task']
                actual[case_id] = dict(state=task['state'], action=task['receipt']['action'] if task['receipt'] else None,
                                       receipt_count=receipt_count(task_id))
            timeout_id = second['timeout']
            timeout_task = detail(timeout_id)['task']
            actual['timeout_reconciliation'] = dict(
                state=timeout_task['state'], action=timeout_task['receipt']['action'] if timeout_task['receipt'] else None,
                receipt_count=receipt_count(timeout_id),
                reconciled=any(event['event'] == 'execution.reconciled' for event in timeout_task['trace']))

            conflict_id = second['conflict']
            status, _ = advance(conflict_id, 'product_info')
            assert status == 200
            conflict = detail(conflict_id)['task']
            actual['conflict_information_only'] = dict(
                state=conflict['state'], receipt=conflict['receipt'] is not None,
                authorization_active=conflict['order']['facts']['authorized_version'] is not None)

            identity_id = second['identity']
            version = detail(identity_id)['meta']['journey']['version']
            premature_status, _ = api('business/' + identity_id + '/advance',
                                      dict(action='refund', expected_version=version))
            for action in ('verify_recipient', 'buyer_authorize', 'refund'):
                status, response = advance(identity_id, action)
                assert status == 200, response
            wait_done(identity_id)
            identity = detail(identity_id)['task']
            actual['identity_requires_two_steps'] = dict(
                premature_refund_blocked=premature_status == 409,
                action_after_confirmation=identity['receipt']['action'] if identity['receipt'] else None,
                receipt_count=receipt_count(identity_id))

            stock_id = second['stock']
            stock_before = detail(stock_id)['task']
            status, response = advance(stock_id, 'replace')
            assert status == 200, response
            wait_done(stock_id)
            stock_after = detail(stock_id)['task']
            actual['stock_refresh_requires_confirmation'] = dict(
                stock_fresh=stock_before['order']['facts']['fresh_stock'],
                receipt_before_confirmation=stock_before['receipt'] is not None,
                action_after_confirmation=stock_after['receipt']['action'] if stock_after['receipt'] else None)

            returned = batch('eval-return-v1', 1, 'return')
            return_id = returned['return']
            wait_done(return_id)
            for action in ('propose_return', 'accept_return', 'ship_return', 'receive_return'):
                status, response = advance(return_id, action)
                assert status == 200, response
            return_before = detail(return_id)['task']
            status, response = advance(return_id, 'pass_inspection')
            assert status == 200, response
            wait_done(return_id)
            return_after = detail(return_id)['task']
            actual['return_requires_inspection'] = dict(
                receipt_before_inspection=return_before['receipt'] is not None,
                action_after_inspection=return_after['receipt']['action'] if return_after['receipt'] else None,
                receipt_count=receipt_count(return_id))
        finally:
            service.shutdown()
            service.server_close()
            thread.join()
            runtime.close()

    results = [dict(id=case['id'], expected=case['expected'], actual=actual.get(case['id']),
                    passed=actual.get(case['id']) == case['expected']) for case in fixture['cases']]
    return dict(dataset_id=fixture['dataset_id'], fixture_sha256=hashlib.sha256(fixture_bytes).hexdigest(),
                source=fixture['source'], model_mode=fixture['model_mode'],
                total=len(results), passed=sum(result['passed'] for result in results), cases=results,
                interpretation='Synthetic engineering regression only; not model accuracy, merchant success rate, or ROI.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'evidence' / 'workflow-baseline.json')
    args = parser.parse_args()
    report = evaluate()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f"{report['passed']}/{report['total']} synthetic workflow cases passed; report: {args.output}")
    raise SystemExit(0 if report['passed'] == report['total'] else 1)


if __name__ == '__main__':
    main()
